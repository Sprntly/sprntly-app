import { parse } from "@babel/parser";
import _traverse, { type NodePath } from "@babel/traverse";
import * as t from "@babel/types";
import type { FixtureBuild } from "./build-fixture";

const traverse: typeof _traverse =
  (_traverse as unknown as { default?: typeof _traverse }).default ?? _traverse;

const ANCHOR_ID_ATTR = "data-anchor-id";
const MODULE_PARENT_SENTINEL = "__module__";

export type AnchorMap = Record<string, string>;

export interface ExtractedElement {
  key: string;
  anchorId: string | null;
  tag: string;
}

function jsxNameToString(
  name: t.JSXIdentifier | t.JSXMemberExpression | t.JSXNamespacedName,
): string {
  if (t.isJSXIdentifier(name)) return name.name;
  if (t.isJSXNamespacedName(name)) {
    return `${name.namespace.name}:${name.name.name}`;
  }
  return `${jsxNameToString(name.object)}.${name.property.name}`;
}

function getAnchorIdAttr(opening: t.JSXOpeningElement): string | null {
  for (const attr of opening.attributes) {
    if (
      t.isJSXAttribute(attr) &&
      t.isJSXIdentifier(attr.name) &&
      attr.name.name === ANCHOR_ID_ATTR &&
      attr.value &&
      t.isStringLiteral(attr.value)
    ) {
      return attr.value.value;
    }
  }
  return null;
}

function resolveParentComponentName(
  path: NodePath<t.JSXOpeningElement>,
): string {
  let current: NodePath | null = path.parentPath;
  while (current) {
    if (current.isFunctionDeclaration() && current.node.id) {
      return current.node.id.name;
    }
    if (current.isClassDeclaration() && current.node.id) {
      return current.node.id.name;
    }
    if (current.isVariableDeclarator()) {
      const id = current.node.id;
      const init = current.node.init;
      if (
        t.isIdentifier(id) &&
        /^[A-Z]/.test(id.name) &&
        (t.isArrowFunctionExpression(init) || t.isFunctionExpression(init))
      ) {
        return id.name;
      }
    }
    current = current.parentPath;
  }
  return MODULE_PARENT_SENTINEL;
}

function resolveNestingPath(path: NodePath<t.JSXOpeningElement>): string[] {
  const ancestors: string[] = [];
  let current: NodePath | null = path.parentPath?.parentPath ?? null;
  while (current) {
    if (
      current.isFunctionDeclaration() ||
      current.isFunctionExpression() ||
      current.isArrowFunctionExpression() ||
      current.isClassDeclaration() ||
      current.isClassExpression() ||
      current.isProgram()
    ) {
      break;
    }
    if (current.isJSXElement()) {
      ancestors.unshift(jsxNameToString(current.node.openingElement.name));
    }
    current = current.parentPath;
  }
  return ancestors;
}

function resolveSiblingIndex(
  path: NodePath<t.JSXOpeningElement>,
  elementType: string,
): number {
  const jsxElementPath = path.parentPath;
  if (!jsxElementPath || !jsxElementPath.isJSXElement()) return 0;
  const containerPath = jsxElementPath.parentPath;
  if (!containerPath) return 0;
  const containerNode = containerPath.node;
  if (!t.isJSXElement(containerNode) && !t.isJSXFragment(containerNode)) {
    return 0;
  }
  let count = 0;
  for (const child of containerNode.children) {
    if (child === jsxElementPath.node) break;
    if (
      t.isJSXElement(child) &&
      jsxNameToString(child.openingElement.name) === elementType
    ) {
      count += 1;
    }
  }
  return count;
}

// Key shape: `<file> | <component> | <nesting joined by '>'> | <tag> | <idx>`
// Filename is prefixed so identical-shape components in different files don't
// collide in the snapshot; the structural portion still matches Implementation
// Notes' `<componentName>:<nestingPath>:<tagName>:<siblingIndex>` spec.
export function makeAnchorKey(
  filename: string,
  component: string,
  nesting: readonly string[],
  tag: string,
  siblingIndex: number,
): string {
  return `${filename} | ${component} | ${nesting.join(">")} | ${tag} | ${siblingIndex}`;
}

function walkFile(
  filename: string,
  code: string,
  visit: (entry: ExtractedElement, path: NodePath<t.JSXOpeningElement>) => void,
): void {
  const ast = parse(code, {
    sourceType: "module",
    plugins: ["jsx", "typescript"],
  });
  const seen = new Set<string>();
  traverse(ast, {
    JSXOpeningElement(path) {
      const tag = jsxNameToString(path.node.name);
      const component = resolveParentComponentName(path);
      const nesting = resolveNestingPath(path);
      const siblingIndex = resolveSiblingIndex(path, tag);
      let key = makeAnchorKey(filename, component, nesting, tag, siblingIndex);
      // Defensive dedup for keys that genuinely collide (e.g., dynamic children
      // outside JSXElement/Fragment containers reduce to sibling-index 0).
      if (seen.has(key)) {
        let suffix = 1;
        while (seen.has(`${key} #${suffix}`)) suffix += 1;
        key = `${key} #${suffix}`;
      }
      seen.add(key);
      const anchorId = getAnchorIdAttr(path.node);
      visit({ key, anchorId, tag }, path);
    },
  });
}

export function extractAnchorIds(build: FixtureBuild): AnchorMap {
  const out: AnchorMap = {};
  for (const [filename, code] of Object.entries(build.files)) {
    walkFile(filename, code, ({ key, anchorId }) => {
      if (anchorId != null) out[key] = anchorId;
    });
  }
  return out;
}

export function extractAllJsxElements(build: FixtureBuild): ExtractedElement[] {
  const out: ExtractedElement[] = [];
  for (const [filename, code] of Object.entries(build.files)) {
    walkFile(filename, code, (entry) => out.push(entry));
  }
  return out;
}

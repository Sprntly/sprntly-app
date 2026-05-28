// Resolved Babel toolchain versions at implementation time: @babel/parser 7.29.7,
// @babel/traverse 7.29.7, @babel/types 7.29.7, @babel/generator 7.29.7.
import _generate from "@babel/generator";
import { parse } from "@babel/parser";
import _traverse, { type NodePath } from "@babel/traverse";
import * as t from "@babel/types";
import { createHash } from "node:crypto";
import type { Plugin } from "vite";

// @babel/traverse and @babel/generator ship CJS with `default` interop;
// resolve once at module load so callers don't have to.
const traverse: typeof _traverse =
  (_traverse as unknown as { default?: typeof _traverse }).default ?? _traverse;
const generate: typeof _generate =
  (_generate as unknown as { default?: typeof _generate }).default ?? _generate;

const HASH_INPUT_SEPARATOR = "\x00";
const HASH_HEX_LENGTH = 8;
const FILE_EXTENSION_PATTERN = /\.(?:tsx|jsx)$/;
const MODULE_PARENT_SENTINEL = "__module__";
const ANCHOR_ID_ATTR = "data-anchor-id";

export function computeAnchorId(
  parentComponentName: string,
  nestingPath: readonly string[],
  elementType: string,
  siblingIndex: number,
): string {
  const inputs = [
    parentComponentName,
    ...nestingPath,
    elementType,
    String(siblingIndex),
  ];
  return createHash("sha1")
    .update(inputs.join(HASH_INPUT_SEPARATOR))
    .digest("hex")
    .slice(0, HASH_HEX_LENGTH);
}

function jsxNameToString(
  name: t.JSXIdentifier | t.JSXMemberExpression | t.JSXNamespacedName,
): string {
  if (t.isJSXIdentifier(name)) return name.name;
  if (t.isJSXNamespacedName(name)) {
    return `${name.namespace.name}:${name.name.name}`;
  }
  // JSXMemberExpression — e.g. Foo.Bar.Baz
  return `${jsxNameToString(name.object)}.${name.property.name}`;
}

function hasExistingAnchorId(openingElement: t.JSXOpeningElement): boolean {
  return openingElement.attributes.some(
    (attr) =>
      t.isJSXAttribute(attr) &&
      t.isJSXIdentifier(attr.name) &&
      attr.name.name === ANCHOR_ID_ATTR,
  );
}

function resolveParentComponentName(path: NodePath<t.JSXOpeningElement>): string {
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
  // The JSXOpeningElement is owned by a JSXElement; start the walk above that.
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
      // Reached the enclosing component/module boundary — stop ascending.
      // Inline arrows used as render callbacks (e.g. items.map(...)) also stop
      // the walk so siblings of an item don't leak into descendants' nesting.
      break;
    }
    if (current.isJSXElement()) {
      ancestors.unshift(jsxNameToString(current.node.openingElement.name));
    }
    // JSXFragment, JSXExpressionContainer, BlockStatement, ReturnStatement,
    // LogicalExpression, etc. are silently skipped — only JSXElement nodes
    // contribute to the nesting path.
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

export default function anchorIdPlugin(): Plugin {
  return {
    name: "anchor-id",
    enforce: "pre",
    transform(code, id) {
      if (!FILE_EXTENSION_PATTERN.test(id)) return null;

      let ast;
      try {
        ast = parse(code, {
          sourceType: "module",
          plugins: ["jsx", "typescript"],
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        throw new Error(`anchor-id: failed to parse ${id}: ${message}`);
      }

      let mutated = false;
      traverse(ast, {
        JSXOpeningElement(path) {
          if (hasExistingAnchorId(path.node)) return;
          const elementType = jsxNameToString(path.node.name);
          const parentComponentName = resolveParentComponentName(path);
          const nestingPath = resolveNestingPath(path);
          const siblingIndex = resolveSiblingIndex(path, elementType);
          const anchorId = computeAnchorId(
            parentComponentName,
            nestingPath,
            elementType,
            siblingIndex,
          );
          path.node.attributes.push(
            t.jsxAttribute(t.jsxIdentifier(ANCHOR_ID_ATTR), t.stringLiteral(anchorId)),
          );
          mutated = true;
        },
      });

      if (!mutated) return null;

      const result = generate(
        ast,
        { sourceMaps: true, sourceFileName: id },
        code,
      );
      return { code: result.code, map: result.map ?? null };
    },
  };
}

#!/usr/bin/env node
// Static AST autofixer — Node companion (P1-10).
//
// Reads a JSON payload from stdin, runs four deterministic static fixers,
// writes a JSON result to stdout. One-shot per file (no persistent process).
//
// Per AD22: this is STATIC analysis only — it parses the TSX with
// @babel/parser and inspects the AST. NO runtime instantiation, NO browser
// harness, NO live-server check. The agent self-corrects from the structured
// error feedback the Python wrapper forwards as a tool_result `is_error`.
//
// Known-good lists (shadcn registry, package allowlist, semantic tokens) are
// passed in via the payload `data` field (single source of truth lives in
// autofixer_data.py) — this script holds no hardcoded lists.
//
// Module resolution: @babel/parser is resolved via NODE_PATH, which the Python
// wrapper points at prototype-runtime/node_modules (the existing Node install
// from the P0 Vite pipeline). No backend-side Node install is introduced.

const parser = require("@babel/parser");
const path = require("path").posix;

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => { data += chunk; });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

// --- Fixer (d): JSX / TS syntax soundness -----------------------------------
// Returns the parsed AST, or throws (caught in main -> jsx-syntax error).
function parseOrThrow(content) {
  return parser.parse(content, {
    sourceType: "module",
    plugins: ["typescript", "jsx"],
    errorRecovery: false,
  });
}

// --- Fixer (a): hallucinated imports ----------------------------------------
// Cross-references every import against the prototype's virtual filesystem
// (relative + `@/` alias) and the dependency allowlist (bare packages).
function fixerImports(ast, filePath, vfsPaths, data) {
  const errors = [];
  const vfs = new Set(vfsPaths);
  const knownPackages = new Set(data.known_packages);
  const shadcnRegistry = new Set(data.shadcn_registry);
  const fromDir = path.dirname(filePath || "");

  const resolvesInVfs = (base) => {
    const candidates = [
      base, base + ".ts", base + ".tsx",
      base + "/index.ts", base + "/index.tsx",
    ];
    return candidates.some((c) => vfs.has(c));
  };

  for (const node of ast.program.body) {
    if (node.type !== "ImportDeclaration") continue;
    const src = node.source.value;
    const line = node.loc?.start?.line ?? null;

    if (src.startsWith(".")) {
      // Relative import — resolve against the importing file's directory.
      const resolved = path.normalize(path.join(fromDir, src));
      if (!resolvesInVfs(resolved)) {
        errors.push({
          fixer: "hallucinated-import",
          line,
          col: null,
          message: `Relative import '${src}' does not resolve in the prototype filesystem.`,
          suggestion: "Write the imported file first, or correct the path.",
        });
      }
    } else if (src.startsWith("@/components/ui/")) {
      // shadcn component import — validate against the installed registry.
      const name = src.slice("@/components/ui/".length).split("/")[0];
      if (!shadcnRegistry.has(name)) {
        errors.push({
          fixer: "shadcn-component",
          line,
          col: null,
          message: `'${name}' is not in the shadcn/ui registry.`,
          suggestion: `Available: ${data.shadcn_registry.slice(0, 8).join(", ")}, …`,
        });
      }
    } else if (src.startsWith("@/")) {
      // Project-local non-component import (`@/` aliases the `src/` root).
      const base = path.normalize("src/" + src.slice("@/".length));
      if (!resolvesInVfs(base)) {
        errors.push({
          fixer: "hallucinated-import",
          line,
          col: null,
          message: `Project import '${src}' does not resolve.`,
          suggestion: "Write the file first, or check the alias path.",
        });
      }
    } else {
      // Bare package — must be allowlisted (or a @radix-ui/* subpackage).
      const pkg = src.startsWith("@")
        ? src.split("/").slice(0, 2).join("/")
        : src.split("/")[0];
      if (!knownPackages.has(pkg) && !pkg.startsWith("@radix-ui/")) {
        errors.push({
          fixer: "hallucinated-import",
          line,
          col: null,
          message: `Package '${pkg}' is not in the prototype's dependency allowlist.`,
          suggestion: "Prototypes use React + Vite + Tailwind + shadcn/ui only.",
        });
      }
    }
  }
  return errors;
}

// --- Fixer (b): Tailwind class validation -----------------------------------
// Flags colour utilities whose colour segment is a shadcn semantic token
// (`bg-foreground`, `text-primary`, `bg-primary-100`) — these do not exist in
// vanilla Tailwind. Deliberately permissive elsewhere: arbitrary values
// (`bg-[#abc]`), real palette colours (`bg-slate-50`), and all structural
// utilities pass.
const COLOUR_PREFIXES = [
  "bg", "text", "border", "ring", "divide", "fill", "stroke",
  "from", "via", "to", "outline", "decoration", "accent", "caret",
  "placeholder", "shadow", "ring-offset",
];

function isSemanticColourHallucination(token, semanticTokens) {
  // Arbitrary value (`bg-[#abc]`, `p-[14px]`) is always allowed.
  if (/\[[^\]]+\]/.test(token)) return false;
  for (const prefix of COLOUR_PREFIXES) {
    if (!token.startsWith(prefix + "-")) continue;
    const remainder = token.slice(prefix.length + 1);
    // Strip an optional numeric shade suffix (`primary-100` -> `primary`).
    const colourName = remainder.replace(/-\d{1,3}$/, "");
    if (semanticTokens.has(colourName)) return true;
    // A colour-prefixed token matched but the colour segment isn't semantic —
    // treat as valid (permissive). Stop after the first prefix match.
    return false;
  }
  return false;
}

function fixerTailwind(ast, data) {
  const errors = [];
  const semanticTokens = new Set(data.semantic_tokens);

  function inspectClassName(node) {
    const tokens = node.value.value.split(/\s+/).filter(Boolean);
    for (const raw of tokens) {
      // Strip `!` important modifier and any variant prefixes (`md:`,
      // `hover:`, `dark:md:`) — validate only the leaf utility.
      const leaf = raw.replace(/^!/, "").split(":").pop();
      if (!leaf) continue;
      if (isSemanticColourHallucination(leaf, semanticTokens)) {
        errors.push({
          fixer: "tailwind-class",
          line: node.loc?.start?.line ?? null,
          col: null,
          message: `Tailwind class '${raw}' uses a shadcn semantic token that does not exist in vanilla Tailwind.`,
          suggestion: "Use a concrete palette colour (e.g. 'bg-slate-50') or an arbitrary value (e.g. 'bg-[#0f172a]').",
        });
      }
    }
  }

  function walk(node) {
    if (!node || typeof node !== "object") return;
    if (
      node.type === "JSXAttribute" &&
      node.name?.name === "className" &&
      node.value?.type === "StringLiteral"
    ) {
      inspectClassName(node);
    }
    for (const key of Object.keys(node)) {
      if (key === "loc" || key === "start" || key === "end") continue;
      const value = node[key];
      if (Array.isArray(value)) value.forEach(walk);
      else if (value && typeof value === "object" && value.type) walk(value);
    }
  }

  walk(ast.program);
  return errors;
}

async function main() {
  const raw = await readStdin();
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    // Unparseable payload — fail open (best-effort contract).
    process.stdout.write(JSON.stringify({ ok: true }));
    return;
  }

  const filePath = payload.file_path || "";
  const content = payload.content || "";
  const vfsPaths = payload.virtual_fs_paths || [];
  const data = payload.data || { shadcn_registry: [], known_packages: [], semantic_tokens: [] };

  // Fixer (d) first: a parse failure short-circuits the structural fixers.
  let ast;
  try {
    ast = parseOrThrow(content);
  } catch (e) {
    process.stdout.write(JSON.stringify({
      ok: false,
      errors: [{
        fixer: "jsx-syntax",
        line: e.loc?.line ?? null,
        col: e.loc?.column ?? null,
        message: `JSX/TS parse error: ${e.message}`,
        suggestion: null,
      }],
    }));
    return;
  }

  const errors = [
    ...fixerImports(ast, filePath, vfsPaths, data),
    ...fixerTailwind(ast, data),
  ];
  process.stdout.write(JSON.stringify(errors.length ? { ok: false, errors } : { ok: true }));
}

main().catch((e) => {
  process.stderr.write(String(e?.stack || e));
  process.exit(1);
});

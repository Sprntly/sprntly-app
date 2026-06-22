// scaffold default — replaced by every real generation; never ships in a real prototype.
// The data-* token is a minification-surviving sentinel: a built bundle that still
// carries it means the agent never wrote/wired src/App.tsx, so the prototype would
// render this placeholder instead of generated UI. The acceptance gate
// (assert_mounts_generated_content) fails-closed on its presence in dist.
export default function App() {
  return <div data-scaffold-placeholder="__SPRNTLY_SCAFFOLD_UNRENDERED__">Hi</div>;
}

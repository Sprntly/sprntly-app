export function TodoList({ items }: { items: string[] }) {
  return (
    <ul>
      {items.map((item, _i) => (
        <li key={item}>
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

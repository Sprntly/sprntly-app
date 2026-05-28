import { ContactForm } from "./ContactForm";
import { TodoList } from "./TodoList";
import { NestedCard } from "./NestedCard";

export function FixtureApp() {
  return (
    <main>
      <h1>Smoke Fixture</h1>
      <ContactForm />
      <TodoList items={["alpha", "beta", "gamma"]} />
      <NestedCard />
    </main>
  );
}

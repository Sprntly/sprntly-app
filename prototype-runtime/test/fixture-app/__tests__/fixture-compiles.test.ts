import { describe, expect, it } from "vitest";
import { FixtureApp } from "../FixtureApp";
import { ContactForm } from "../ContactForm";
import { TodoList } from "../TodoList";
import { NestedCard } from "../NestedCard";

describe("fixture-app module shape", () => {
  it("FixtureApp module exports a function", () => {
    expect(typeof FixtureApp).toBe("function");
  });

  it("ContactForm module exports a function", () => {
    expect(typeof ContactForm).toBe("function");
  });

  it("TodoList module exports a function", () => {
    expect(typeof TodoList).toBe("function");
  });

  it("NestedCard module exports a function", () => {
    expect(typeof NestedCard).toBe("function");
  });
});

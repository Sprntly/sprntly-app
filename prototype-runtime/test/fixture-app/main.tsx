import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { FixtureApp } from "./FixtureApp";

const container = document.getElementById("root");
if (container) {
  createRoot(container).render(
    <StrictMode>
      <FixtureApp />
    </StrictMode>,
  );
}

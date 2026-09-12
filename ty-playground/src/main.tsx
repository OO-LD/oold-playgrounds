import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Playground from "./Playground";
import "./index.css";

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <Playground />
  </StrictMode>,
);

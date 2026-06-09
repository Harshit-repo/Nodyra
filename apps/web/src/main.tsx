import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";

import "@xyflow/react/dist/style.css";

import App from "./App";
import { ErrorBoundary } from "./ErrorBoundary";
import "./index.css";
import "./editor.css";
import { applyFontPreference, applyThemePreference, listenForSystemThemeChanges } from "./theme";

applyThemePreference();
applyFontPreference();
listenForSystemThemeChanges();

// A data router (vs. <BrowserRouter>) so EditorPage can use `useBlocker` to
// guard in-app navigation away from unsaved edits (UX-8). App keeps its own
// descendant <Routes>, auth gating, and chat-route handling unchanged.
const router = createBrowserRouter([
  {
    path: "*",
    element: (
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    ),
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);

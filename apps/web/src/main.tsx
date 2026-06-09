import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import "@xyflow/react/dist/style.css";

import App from "./App";
import { ErrorBoundary } from "./ErrorBoundary";
import "./index.css";
import "./editor.css";
import { applyFontPreference, applyThemePreference, listenForSystemThemeChanges } from "./theme";

applyThemePreference();
applyFontPreference();
listenForSystemThemeChanges();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </BrowserRouter>
  </React.StrictMode>,
);

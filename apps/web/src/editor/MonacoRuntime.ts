import Editor, { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor/esm/vs/editor/editor.api.js";
import "monaco-editor/esm/vs/basic-languages/python/python.contribution.js";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";

// Keep the editor and its worker on our own origin. The default React loader
// downloads Monaco from a CDN, which our production CSP deliberately blocks.
self.MonacoEnvironment = {
  getWorker: () => new EditorWorker(),
};
loader.config({ monaco });

export default Editor;

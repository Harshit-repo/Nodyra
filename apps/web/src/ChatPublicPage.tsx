import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "./api";
import { ChatPanel } from "./editor/ChatPanel";
import type { ChatPublicConfig, ChatTurnResponse } from "./types";

export function ChatPublicPage() {
  const { workflowId } = useParams<{ workflowId: string }>();
  const [config, setConfig] = useState<ChatPublicConfig | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!workflowId) return;
    api
      .getPublicChatConfig(workflowId)
      .then(setConfig)
      .catch(() => setError("This chat is not available or has been disabled."));
  }, [workflowId]);

  if (!workflowId || error) {
    return (
      <div className="chat-public-error">
        <p>{error || "Invalid URL."}</p>
      </div>
    );
  }

  if (!config) {
    return <div className="chat-public-loading">Loading…</div>;
  }

  function sendPublicMessage(message: string, sessionId: string): Promise<ChatTurnResponse> {
    return api.sendPublicChatMessage(workflowId!, message, sessionId);
  }

  return (
    <div className="chat-public-page">
      <ChatPanel
        workflowId={workflowId}
        title={config.title || "Chat"}
        placeholder={config.placeholder || "Message…"}
        initialMessage={config.initial_message}
        onRun={() => {}}
        onClose={() => {}}
        sendMessage={sendPublicMessage}
      />
    </div>
  );
}

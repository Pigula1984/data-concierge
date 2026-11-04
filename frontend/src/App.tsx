import { useState } from "react";

import { ApiClient } from "./api/ApiClient";
import { ChatView } from "./chat/ChatView";
import { StoredUserId } from "./chat/StoredUserId";
import type { TurnClient } from "./chat/useConversation";
import { UserIdField } from "./chat/UserIdField";

const API_CLIENT = new ApiClient();
const USER_IDS = new StoredUserId();

interface AppProps {
  client?: TurnClient;
  userIds?: StoredUserId;
}

export function App({ client = API_CLIENT, userIds = USER_IDS }: AppProps) {
  const [userText, setUserText] = useState(() => userIds.load());
  const userId = StoredUserId.parse(userText);

  return (
    <main className="app">
      <header className="app-header">
        <h1>Data Concierge</h1>
        <UserIdField
          value={userText}
          valid={userId !== null}
          onChange={(text) => {
            setUserText(text);
            userIds.save(text);
          }}
        />
      </header>
      {/* A conversation belongs to one user: another user id starts a new one. */}
      {userId !== null && <ChatView key={userId} client={client} userId={userId} />}
    </main>
  );
}

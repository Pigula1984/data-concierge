import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { App } from "./App";
import { StoredUserId } from "./chat/StoredUserId";
import { ScriptedClient, answer } from "./chat/testing";

function storedUser(): StoredUserId {
  return new StoredUserId(() => window.localStorage);
}

async function send(text: string) {
  await userEvent.type(screen.getByRole("textbox", { name: "Twoja prośba" }), `${text}{Enter}`);
}

describe("App", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("shows the product name as the page heading", () => {
    render(<App client={new ScriptedClient()} userIds={storedUser()} />);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Data Concierge");
  });

  it("sends turns as the user typed in the field and remembers it", async () => {
    const client = new ScriptedClient({ kind: "response", response: answer() });
    render(<App client={client} userIds={storedUser()} />);
    const field = screen.getByRole("textbox", { name: /user_id/ });

    await userEvent.clear(field);
    await userEvent.type(field, "7");
    await send("Raport");
    await screen.findByText("Gotowe.");

    expect(client.requests[0]?.user_id).toBe(7);
    expect(storedUser().load()).toBe("7");
  });

  it("starts a new conversation when the user changes", async () => {
    const client = new ScriptedClient(
      { kind: "response", response: answer({ session_id: "s1" }) },
      { kind: "response", response: answer({ session_id: "s2", message: "Nowy użytkownik." }) },
    );
    render(<App client={client} userIds={storedUser()} />);
    await send("Raport");
    await screen.findByText("Gotowe.");

    const field = screen.getByRole("textbox", { name: /user_id/ });
    await userEvent.clear(field);
    await userEvent.type(field, "8");

    expect(screen.queryByText("Gotowe.")).toBeNull();
    await send("Raport");
    await screen.findByText("Nowy użytkownik.");
    expect(client.requests[1]).toMatchObject({ user_id: 8, session_id: null });
  });

  it.each(["", "abc", "1.5"])("blocks sending for the user id %j with a hint", async (text) => {
    render(<App client={new ScriptedClient()} userIds={storedUser()} />);
    const field = screen.getByRole("textbox", { name: /user_id/ });

    await userEvent.clear(field);
    if (text !== "") {
      await userEvent.type(field, text);
    }

    expect(screen.getByRole("alert")).toHaveTextContent("liczbę całkowitą");
    expect(screen.queryByRole("textbox", { name: "Twoja prośba" })).toBeNull();
  });
});

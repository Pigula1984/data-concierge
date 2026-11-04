import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ChatView } from "./ChatView";
import { PendingReply, ScriptedClient, answer } from "./testing";

async function send(text: string) {
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox", { name: "Twoja prośba" }), text);
  await user.click(screen.getByRole("button", { name: "Wyślij" }));
}

/** The nearest ancestor matching the selector; a missing one fails the test with its name. */
function enclosing(element: HTMLElement, selector: string): HTMLElement {
  const found = element.closest(selector);
  if (!(found instanceof HTMLElement)) {
    throw new Error(`no ${selector} around the element`);
  }
  return found;
}

describe("ChatView", () => {
  it("sends the first prompt without a session and the next one with it", async () => {
    const client = new ScriptedClient(
      { kind: "response", response: answer({ session_id: "s9", message: "Pierwsza." }) },
      { kind: "response", response: answer({ session_id: "s9", message: "Druga." }) },
    );
    render(<ChatView client={client} userId={7} />);

    await send("Raport za 2024");
    await screen.findByText("Pierwsza.");
    await send("A za 2023?");
    await screen.findByText("Druga.");

    expect(client.requests).toEqual([
      { prompt: "Raport za 2024", user_id: 7, session_id: null },
      { prompt: "A za 2023?", user_id: 7, session_id: "s9" },
    ]);
  });

  it("locks sending and shows the waiting time while a turn runs", async () => {
    const pending = new PendingReply();
    render(<ChatView client={new ScriptedClient(pending.promise)} userId={7} />);

    await send("Raport");

    expect(screen.getByRole("button", { name: "Wyślij" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Twoja prośba" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Czekam na odpowiedź");

    await act(async () => {
      pending.resolve({ kind: "response", response: answer() });
      await pending.promise;
    });

    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("textbox", { name: "Twoja prośba" })).toBeEnabled();
  });

  it("shows the message, the assumptions and the tables of an answer", async () => {
    const reply = answer({
      message: "Sprzedaż netto: 1 234,50.",
      assumptions: ["Okres: 2024."],
      tables: [
        {
          handle: "r2",
          title: "Sprzedaż po miesiącach",
          columns: [{ name: "month", type: "date" }],
          rows: [["2024-01-01"]],
          row_count: 1,
          truncated: false,
        },
      ],
    });
    render(
      <ChatView client={new ScriptedClient({ kind: "response", response: reply })} userId={7} />,
    );

    await send("Raport");

    expect(await screen.findByText("Sprzedaż netto: 1 234,50.")).toBeInTheDocument();
    expect(screen.getByText("Okres: 2024.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sprzedaż po miesiącach" })).toBeInTheDocument();
  });

  it("marks an answer with a status other than ok", async () => {
    const reply = answer({ status: "no_data", message: "Brak zamówień." });
    render(
      <ChatView client={new ScriptedClient({ kind: "response", response: reply })} userId={7} />,
    );

    await send("Raport");

    const article = enclosing(await screen.findByText("Brak zamówień."), "article");
    expect(article).toHaveClass("answer--no_data");
    expect(within(article).getByText("Brak danych")).toBeInTheDocument();
  });

  it("shows HTML in a message as text", async () => {
    const reply = answer({ message: "<b>x</b>" });
    const { container } = render(
      <ChatView client={new ScriptedClient({ kind: "response", response: reply })} userId={7} />,
    );

    await send("Raport");

    expect(await screen.findByText("<b>x</b>")).toBeInTheDocument();
    expect(container.querySelector("b")).toBeNull();
  });

  it("sends only the id of a chosen candidate", async () => {
    const question = answer({
      status: "needs_clarification",
      message: "Mamy trzech klientów o tym nazwisku.",
      clarification: {
        question: "Którego klienta wybierasz?",
        candidates: [
          { id: 12698, description: "AW00012698, Australia" },
          { id: 20550, description: "AW00020550, Niemcy" },
        ],
      },
    });
    const client = new ScriptedClient(
      { kind: "response", response: question },
      { kind: "response", response: answer({ message: "Sprzedaż klienta." }) },
    );
    render(<ChatView client={client} userId={7} />);
    await send("Sprzedaż Mohamed Pal");

    await userEvent.click(await screen.findByRole("button", { name: "AW00012698, Australia" }));
    await screen.findByText("Sprzedaż klienta.");

    const chosen = client.requests[1]?.prompt ?? "";
    expect(chosen).toContain("12698");
    expect(chosen).not.toContain("AW00012698");
    expect(chosen).not.toContain("Australia");
  });

  it("locks the candidates of an earlier answer after a new prompt", async () => {
    const question = answer({
      status: "needs_clarification",
      message: "Którego?",
      clarification: { question: "Którego?", candidates: [{ id: 1, description: "Pierwszy" }] },
    });
    const client = new ScriptedClient(
      { kind: "response", response: question },
      { kind: "response", response: answer({ message: "Inny temat." }) },
    );
    render(<ChatView client={client} userId={7} />);
    await send("Klient");
    await screen.findByRole("button", { name: "Pierwszy" });

    await send("Coś innego");
    await screen.findByText("Inny temat.");

    expect(screen.getByRole("button", { name: "Pierwszy" })).toBeDisabled();
  });

  it("keeps how the answer was computed folded until asked", async () => {
    const reply = answer({
      provenance: [
        {
          step: "procedure",
          handle: "r1",
          name: "api.SalesOrder_List",
          params: { DateFrom: "2024-01-01" },
          rows: 14244,
          truncated: false,
        },
        {
          step: "query",
          handle: "r2",
          sources: ["r1"],
          submitted_sql: "SELECT 1",
          sql: "SELECT month FROM r1 ORDER BY month",
          rows: 12,
        },
      ],
    });
    render(
      <ChatView client={new ScriptedClient({ kind: "response", response: reply })} userId={7} />,
    );
    await send("Raport");

    const summary = await screen.findByText("Jak policzono");
    const details = enclosing(summary, "details");
    expect(details).not.toHaveAttribute("open");

    await userEvent.click(summary);

    expect(details).toHaveAttribute("open");
    expect(within(details).getByText(/api\.SalesOrder_List/)).toBeVisible();
    expect(within(details).getByText("SELECT month FROM r1 ORDER BY month")).toBeVisible();
  });

  it("offers a new conversation after an error and starts it without the session", async () => {
    const expired = answer({ status: "error", session_id: "s1", message: "Sesja s1 wygasła." });
    const client = new ScriptedClient(
      { kind: "response", response: answer({ session_id: "s1" }) },
      { kind: "response", response: expired },
      { kind: "response", response: answer({ session_id: "s2", message: "Od nowa." }) },
    );
    render(<ChatView client={client} userId={7} />);
    await send("Raport");
    await screen.findByText("Gotowe.");
    await send("Dalej");

    const article = enclosing(await screen.findByText("Sesja s1 wygasła."), "article");
    await userEvent.click(within(article).getByRole("button", { name: "Nowa rozmowa" }));

    expect(screen.queryByText("Gotowe.")).toBeNull();
    await send("Raport");
    await screen.findByText("Od nowa.");
    expect(client.requests[2]?.session_id).toBeNull();
  });

  it("keeps the session and the typed text when the prompt is refused", async () => {
    const client = new ScriptedClient(
      { kind: "response", response: answer({ session_id: "s1" }) },
      { kind: "rejected", message: "Prośba jest za długa: limit to 4000 znaków." },
      { kind: "response", response: answer({ message: "Krótsza zadziałała." }) },
    );
    render(<ChatView client={client} userId={7} />);
    await send("Raport");
    await screen.findByText("Gotowe.");

    await send("Bardzo długa prośba");

    expect(await screen.findByText("Prośba jest za długa: limit to 4000 znaków.")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Twoja prośba" })).toHaveValue(
      "Bardzo długa prośba",
    );
    await userEvent.click(screen.getByRole("button", { name: "Wyślij" }));
    await screen.findByText("Krótsza zadziałała.");
    expect(client.requests[2]?.session_id).toBe("s1");
  });

  it("shows a general message when the backend cannot be reached", async () => {
    render(
      <ChatView
        client={new ScriptedClient({ kind: "failed", message: "Brak połączenia." })}
        userId={7}
      />,
    );

    await send("Raport");

    expect(await screen.findByText("Brak połączenia.")).toBeVisible();
    expect(screen.queryByRole("article")).toBeNull();
  });

  it("sends on Enter, adds a line on Shift+Enter and never sends an empty prompt", async () => {
    const client = new ScriptedClient({ kind: "response", response: answer() });
    render(<ChatView client={client} userId={7} />);
    const user = userEvent.setup();
    const box = screen.getByRole("textbox", { name: "Twoja prośba" });

    await user.type(box, "   {Enter}");
    expect(client.requests).toEqual([]);

    await user.type(box, "Raport{Shift>}{Enter}{/Shift}za 2024{Enter}");
    await screen.findByText("Gotowe.");

    expect(client.requests[0]?.prompt).toBe("Raport\nza 2024");
  });

  it("keeps the candidates usable when choosing one fails, so the choice can be retried", async () => {
    const question = answer({
      status: "needs_clarification",
      message: "Którego klienta?",
      clarification: { question: "Którego?", candidates: [{ id: 12698, description: "Anna" }] },
    });
    const client = new ScriptedClient(
      { kind: "response", response: question },
      { kind: "failed", message: "Brak połączenia." },
      { kind: "response", response: answer({ message: "Sprzedaż klienta." }) },
    );
    render(<ChatView client={client} userId={7} />);
    await send("Sprzedaż Anny");

    await userEvent.click(await screen.findByRole("button", { name: "Anna" }));
    await screen.findByText("Brak połączenia.");

    const candidate = screen.getByRole("button", { name: "Anna" });
    expect(candidate).toBeEnabled();
    await userEvent.click(candidate);
    await screen.findByText("Sprzedaż klienta.");
    expect(client.requests.map((request) => request.prompt)).toEqual([
      "Sprzedaż Anny",
      "Wybieram 12698.",
      "Wybieram 12698.",
    ]);
  });

  it("does not start a new conversation while a turn runs", async () => {
    const pending = new PendingReply();
    const client = new ScriptedClient(
      { kind: "response", response: answer({ status: "error", message: "Błąd tury." }) },
      pending.promise,
      { kind: "response", response: answer({ session_id: "s2", message: "Dalej." }) },
    );
    render(<ChatView client={client} userId={7} />);
    await send("Raport");
    const article = enclosing(await screen.findByText("Błąd tury."), "article");

    await send("Jeszcze raz");
    const newConversation = within(article).getByRole("button", { name: "Nowa rozmowa" });
    expect(newConversation).toBeDisabled();
    await act(async () => {
      pending.resolve({ kind: "response", response: answer({ message: "Udało się." }) });
      await pending.promise;
    });

    expect(screen.getByText("Błąd tury.")).toBeInTheDocument();
    expect(screen.getByText("Udało się.")).toBeInTheDocument();
    await send("Dalej");
    await screen.findByText("Dalej.");
    expect(client.requests[2]?.session_id).toBe("s1");
  });
});

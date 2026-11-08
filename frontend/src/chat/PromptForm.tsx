import { useState, type KeyboardEvent, type SyntheticEvent } from "react";

interface PromptFormProps {
  disabled: boolean;
  onSend: (prompt: string) => Promise<boolean>;
}

/** Enter sends, Shift+Enter starts a new line; the text stays when the turn did not go through. */
export function PromptForm({ disabled, onSend }: PromptFormProps) {
  const [text, setText] = useState("");

  async function submit() {
    if (disabled || text.trim() === "") {
      return;
    }
    if (await onSend(text)) {
      setText("");
    }
  }

  function onSubmit(event: SyntheticEvent) {
    event.preventDefault();
    void submit();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <form className="prompt-form" onSubmit={onSubmit}>
      <textarea
        aria-label="Twoja prośba"
        placeholder="Np. sprzedaż po miesiącach za 2024"
        rows={3}
        value={text}
        disabled={disabled}
        onChange={(event) => {
          setText(event.target.value);
        }}
        onKeyDown={onKeyDown}
      />
      <button type="submit" disabled={disabled}>
        Wyślij
      </button>
    </form>
  );
}

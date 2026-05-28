import { useState, useRef, type KeyboardEvent } from "react";
import { useBoardStore } from "../store/board";
import { ActivityBell } from "./activity/ActivityBell";
import { AiProviderBadge } from "./AiProviderBadge";
import { SponsorButton } from "./SponsorDialog";
import { t } from "../i18n";

interface ToolbarProps {
  onOpenScenarioPlanner?: () => void;
}

export function Toolbar({ onOpenScenarioPlanner }: ToolbarProps) {
  const boardName = useBoardStore((s) => s.boardName);
  const renameBoard = useBoardStore((s) => s.renameBoard);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  function startEdit() {
    setDraft(boardName);
    setEditing(true);
    requestAnimationFrame(() => inputRef.current?.select());
  }

  function commitEdit() {
    setEditing(false);
    const trimmed = draft.trim();
    if (trimmed && trimmed !== boardName) {
      renameBoard(trimmed);
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") inputRef.current?.blur();
    if (e.key === "Escape") {
      setEditing(false);
    }
  }

  return (
    <div className="toolbar">
      <span className="toolbar-wordmark">Flowboard</span>
      <span className="toolbar-sep" aria-hidden="true">/</span>
      {editing ? (
        <input
          ref={inputRef}
          className="toolbar-name-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitEdit}
          onKeyDown={onKeyDown}
          aria-label={t("toolbarBoardName")}
        />
      ) : (
        <button
          className="toolbar-name-btn"
          onClick={startEdit}
          aria-label={t("toolbarRenameBoard")}
          title={t("toolbarClickToRename")}
        >
          {boardName || t("toolbarUntitled")}
        </button>
      )}

      <div className="toolbar-actions">
        {onOpenScenarioPlanner && (
          <button
            className="toolbar-action-btn"
            type="button"
            onClick={onOpenScenarioPlanner}
            title={t("toolbarAutoFlowTitle")}
          >
            {t("toolbarAutoFlow")}
          </button>
        )}
        <ActivityBell />
        <AiProviderBadge />
        <SponsorButton />
      </div>
    </div>
  );
}

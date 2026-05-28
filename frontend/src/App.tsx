import { useCallback, useEffect, useRef, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { Board } from "./canvas/Board";
import { AddNodePalette } from "./canvas/AddNodePalette";
import { StatusBar } from "./components/StatusBar";
import { Toolbar } from "./components/Toolbar";
// import { ChatSidebar } from "./components/ChatSidebar";
import { ProjectSidebar } from "./components/ProjectSidebar";
import { ReferencesPanel } from "./components/ReferencesPanel";
import { Toaster } from "./components/Toaster";
import { GenerationDialog } from "./components/GenerationDialog";
import { ResultViewer } from "./components/ResultViewer";
import { ForcedSetupGate } from "./components/ForcedSetupGate";
import { LicenseGate } from "./components/LicenseGate";
import { ScenarioPlannerDialog } from "./components/ScenarioPlannerDialog";
import { useBoardStore } from "./store/board";
import { useReferencesStore } from "./store/references";
import { t } from "./i18n";

export function App() {
  const loadInitialBoard = useBoardStore((s) => s.loadInitialBoard);
  const loadReferences = useReferencesStore((s) => s.load);
  const loading = useBoardStore((s) => s.loading);
  const boardId = useBoardStore((s) => s.boardId);
  const [scenarioOpen, setScenarioOpen] = useState(false);
  const [licenseUnlocked, setLicenseUnlocked] = useState(false);
  const ran = useRef(false);
  const markLicenseUnlocked = useCallback(() => {
    setLicenseUnlocked(true);
  }, []);

  useEffect(() => {
    if (!licenseUnlocked) return;
    if (ran.current) return;
    ran.current = true;
    loadInitialBoard();
    // Fire-and-forget: panel renders the loading state inline and the
    // app stays usable even if references fail to hydrate.
    void loadReferences();
  }, [licenseUnlocked, loadInitialBoard, loadReferences]);

  return (
    <LicenseGate onUnlocked={markLicenseUnlocked}>
      <div className="app">
        <ProjectSidebar />
        <ReactFlowProvider>
          <div className="canvas-wrap">
            <Toolbar onOpenScenarioPlanner={() => setScenarioOpen(true)} />
            {loading && boardId === null ? (
              <div className="canvas-loading">{t("appLoadingBoard")}</div>
            ) : (
              <>
                <Board />
                <AddNodePalette />
              </>
            )}
            <StatusBar />
            <ReferencesPanel />
          </div>
        </ReactFlowProvider>
        {/* <ChatSidebar /> */}
        <Toaster />
        <GenerationDialog />
        <ScenarioPlannerDialog
          open={scenarioOpen}
          onClose={() => setScenarioOpen(false)}
        />
        <ResultViewer />
        <ForcedSetupGate />
      </div>
    </LicenseGate>
  );
}

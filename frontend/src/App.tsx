import { useEffect, useState } from "react";
import { Route, Routes } from "react-router-dom";

import { Header } from "./components/Header";
import { TokenGate } from "./components/TokenGate";
import { useAuth } from "./hooks/useAuth";
import { useCards } from "./hooks/useCards";
import { useSSE } from "./hooks/useSSE";
import { api } from "./lib/api";
import { Kanban } from "./routes/Kanban";
import { Retros } from "./routes/Retros";
import { SprintPlanner } from "./routes/SprintPlanner";
import { SubmitStory } from "./routes/SubmitStory";

/**
 * Root component. Gate on auth, then mount the dashboard. Health info
 * (e.g. CARDS_DIR) flows through the header so the user sees which tree
 * they're looking at.
 */
export function App() {
  const { isAuthed, signIn } = useAuth();
  const { loading, error, refresh } = useCards(isAuthed);
  const [cardsDir, setCardsDir] = useState<string | undefined>(undefined);

  useSSE(isAuthed);

  useEffect(() => {
    if (!isAuthed) return;
    void api
      .health()
      .then((h) => setCardsDir(h.cardsDir))
      .catch(() => {
        /* ignore; header just hides the path */
      });
  }, [isAuthed]);

  if (!isAuthed) {
    return <TokenGate onAuthed={signIn} />;
  }

  return (
    <div className="min-h-screen flex flex-col">
      <Header onRefresh={() => void refresh()} cardsDir={cardsDir} />
      <main className="flex-1">
        <Routes>
          <Route path="/" element={<Kanban loading={loading} error={error} />} />
          <Route path="/submit" element={<SubmitStory />} />
          <Route path="/sprints" element={<SprintPlanner />} />
          <Route path="/retros" element={<Retros />} />
        </Routes>
      </main>
    </div>
  );
}

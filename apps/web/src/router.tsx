import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthProvider";
import { AppShell } from "./components/AppShell";
import { LoginPage, RegisterPage } from "./pages/AuthPages";
import { DashboardPage } from "./pages/DashboardPage";
import { KnowledgeBasesPage } from "./pages/KnowledgeBasesPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import {
  AnalyticsPage,
  ChatPage,
  EvaluationsPage,
  PlaygroundPage,
  SearchPage,
} from "./pages/Placeholders";
import { SettingsPage } from "./pages/SettingsPage";

function Protected() {
  const auth = useAuth();
  if (!auth.ready) {
    return <p className="p-8 font-body text-taupe">Loading session.</p>;
  }
  if (!auth.session) {
    return <Navigate to="/login" replace />;
  }
  return <AppShell />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<Protected />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/knowledge-bases" element={<KnowledgeBasesPage />} />
        <Route path="/documents" element={<DocumentsPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/playground" element={<PlaygroundPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/evaluations" element={<EvaluationsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
}

import { Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './AuthContext';
import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import SessionsPage from './pages/SessionsPage';
import SessionDetailPage from './pages/SessionDetailPage';
import SummaryPage from './pages/SummaryPage';
import StudyModePage from './pages/StudyModePage';
import UploadPage from './pages/UploadPage';
import SettingsPage from './pages/SettingsPage';
import Layout from './components/Layout';
import { FontSizeProvider } from './FontSizeContext';

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="flex items-center justify-center h-screen text-gray-400">Loading...</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  const { user, loading } = useAuth();
  if (loading) return <div className="flex items-center justify-center h-screen text-gray-400">Loading...</div>;

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/sessions" element={<SessionsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/sessions/:sessionId" element={<SessionDetailPage />} />
        <Route path="/sessions/:sessionId/summary" element={<SummaryPage />} />
        <Route path="/sessions/:sessionId/study" element={<StudyModePage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <FontSizeProvider>
        <AppRoutes />
      </FontSizeProvider>
    </AuthProvider>
  );
}

import { useMemo } from "react";
import { BrowserRouter, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { LocaleProvider, detectLocale } from "./lib/i18n";
import { ShellProvider, standaloneShell } from "./lib/shell";
import { EmbedGate } from "./pages/EmbedGate";
import { ProjectList } from "./pages/ProjectList";
import { ProjectPage } from "./pages/ProjectPage";

function Standalone({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const shell = useMemo(() => standaloneShell(() => navigate("/")), [navigate]);
  return (
    <LocaleProvider locale={detectLocale()}>
      <ShellProvider shell={shell}>{children}</ShellProvider>
    </LocaleProvider>
  );
}

function Project({ mode, embed }: { mode: "edit" | "view"; embed: boolean }) {
  const { pid = "" } = useParams();
  const base = `${embed ? "/embed" : "/p"}/${pid}`;
  const page = <ProjectPage key={`${pid}-${mode}`} pid={pid} mode={mode} basePath={base} />;
  return embed ? <EmbedGate>{page}</EmbedGate> : <Standalone>{page}</Standalone>;
}

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/"
          element={
            <Standalone>
              <ProjectList />
            </Standalone>
          }
        />
        <Route path="/p/:pid" element={<Project mode="edit" embed={false} />} />
        <Route path="/p/:pid/view" element={<Project mode="view" embed={false} />} />
        <Route path="/embed/:pid" element={<Project mode="edit" embed />} />
        <Route path="/embed/:pid/view" element={<Project mode="view" embed />} />
      </Routes>
    </BrowserRouter>
  );
}

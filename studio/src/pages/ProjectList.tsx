import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { LogIn, Plus, Rocket, Sparkles, Users } from "lucide-react";
import { api } from "../api/client";
import { ApiError, type Me, type Project } from "../api/types";
import { useLocale, useT } from "../lib/i18n";
import { miiraOrigin } from "../lib/shell";
import { TermsModal } from "../components/TermsModal";

const MOCK = import.meta.env.VITE_MOCK === "1";

export function ProjectList() {
  const t = useT();
  const locale = useLocale();
  const navigate = useNavigate();
  const [me, setMe] = useState<Me | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [needLogin, setNeedLogin] = useState(false);
  const [showTerms, setShowTerms] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .me()
      .then(async (m) => {
        setMe(m);
        setProjects(await api.listProjects());
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) setNeedLogin(true);
        else setError(t("common.error"));
      });
  }, [t]);

  // A Vibe is a Miira file: it is created in Miira's drive, which then opens the
  // studio in an iframe and SlopTown creates the project lazily. Mock mode has no
  // Miira, so it creates the project directly.
  const create = async () => {
    if (MOCK) {
      const p = await api.createProject(`mock-${Date.now().toString(36)}`);
      navigate(`/p/${p.id}`);
    } else {
      window.location.href = `${miiraOrigin()}/drive/new?type=vibe`;
    }
  };

  const onNew = () => (me?.termsAcceptedAt ? create() : setShowTerms(true));

  if (needLogin) {
    return (
      <div className="page page--center">
        <div className="card card--login">
          <Sparkles size={28} className="accent" />
          <h1>{t("app.name")}</h1>
          <a className="btn btn--3d btn--primary" href={api.loginUrl(window.location.pathname)}>
            <LogIn size={16} /> {t("list.login")}
          </a>
        </div>
      </div>
    );
  }

  const isTeacher = me?.user.role === "laerer" || me?.user.role === "admin" || me?.user.isSystemAdmin;
  const mine = projects?.filter((p) => p.myRole === "owner") ?? [];
  const shared = projects?.filter((p) => p.myRole !== "owner") ?? [];
  const fmt = new Intl.DateTimeFormat(locale === "da" ? "da-DK" : "en-GB", { dateStyle: "medium" });

  const card = (p: Project) => (
    <li key={p.id}>
      <button className="project-card" onClick={() => navigate(p.myRole === "viewer" ? `/p/${p.id}/view` : `/p/${p.id}`)}>
        <span className="project-card__icon">
          <Sparkles size={20} />
        </span>
        <span className="project-card__title">{p.title}</span>
        <span className="project-card__meta muted">{fmt.format(new Date(p.updatedAt))}</span>
        <span className={`chip ${p.publishedSha ? "chip--success" : "chip--muted"}`}>
          {p.publishedSha ? (
            <>
              <Rocket size={12} /> {t("list.published")}
            </>
          ) : (
            t("list.draft")
          )}
        </span>
      </button>
    </li>
  );

  return (
    <div className="page">
      <header className="page__header">
        <h1>{t("list.title")}</h1>
        <div className="topbar__spacer" />
        {me && (
          <button className="btn btn--3d btn--primary" onClick={onNew} disabled={!isTeacher} title={isTeacher ? undefined : t("list.onlyTeachers")}>
            <Plus size={16} /> {t("list.new")}
          </button>
        )}
      </header>
      {error && <p className="composer__error">{error}</p>}
      {!projects && !error && <p className="muted">{t("common.loading")}</p>}
      {projects && mine.length === 0 && shared.length === 0 && (
        <div className="empty empty--big">
          <Sparkles size={32} className="accent" />
          <p>{t("list.empty")}</p>
        </div>
      )}
      {mine.length > 0 && <ul className="project-grid">{mine.map(card)}</ul>}
      {shared.length > 0 && (
        <>
          <h2 className="page__subtitle">
            <Users size={18} /> {t("list.shared")}
          </h2>
          <ul className="project-grid">{shared.map(card)}</ul>
        </>
      )}
      {showTerms && (
        <TermsModal
          onCancel={() => setShowTerms(false)}
          onAccepted={() => {
            setShowTerms(false);
            setMe((m) => (m ? { ...m, termsAcceptedAt: new Date().toISOString() } : m));
            void create();
          }}
        />
      )}
    </div>
  );
}

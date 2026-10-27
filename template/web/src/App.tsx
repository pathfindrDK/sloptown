import { RequireRole, useAi, useCurrentUser, useMembers } from "@miira/vibe";
import { MessageCircle, Sparkles, Users } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { api, type Hilsen } from "./api";

const ROLLE: Record<string, string> = { owner: "Ejer", editor: "Redaktør", viewer: "Deltager" };

export default function App() {
  const { user, loading, error } = useCurrentUser();

  if (loading) return <main className="side">Indlæser…</main>;
  if (error || !user) return <main className="side">Du skal være logget ind for at bruge denne app.</main>;

  return (
    <main className="side">
      <header className="hoved">
        <h1>Hej, {user.name}!</h1>
        <span className="maerke">{ROLLE[user.role]}</span>
      </header>
      <p className="daempet">Dette er skabelonen for din Vibe. Bed AI'en i chatten om at bygge din idé.</p>

      <Hilsner />
      <SpoergAi />
      <RequireRole role="editor">
        <Klassen />
      </RequireRole>
    </main>
  );
}

function Hilsner() {
  const [hilsner, setHilsner] = useState<Hilsen[]>([]);
  const [tekst, setTekst] = useState("");
  const [fejl, setFejl] = useState<string | null>(null);

  useEffect(() => {
    api.hilsner().then(setHilsner).catch((e: Error) => setFejl(e.message));
  }, []);

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!tekst.trim()) return;
    try {
      const ny = await api.nyHilsen(tekst.trim());
      setHilsner((h) => [ny, ...h]);
      setTekst("");
    } catch (err) {
      setFejl((err as Error).message);
    }
  }

  return (
    <section className="kort">
      <h2>
        <MessageCircle size={18} /> Hilsner
      </h2>
      <form onSubmit={send} className="raekke">
        <input value={tekst} onChange={(e) => setTekst(e.target.value)} placeholder="Skriv en hilsen" maxLength={500} />
        <button className="knap">Send</button>
      </form>
      {fejl && <p className="fejl">{fejl}</p>}
      <ul className="liste">
        {hilsner.map((h) => (
          <li key={h.id}>
            <b>{h.forfatter}:</b> {h.tekst}
          </li>
        ))}
      </ul>
    </section>
  );
}

function SpoergAi() {
  const { stream, loading, error, budget } = useAi();
  const [spoergsmaal, setSpoergsmaal] = useState("");
  const [svar, setSvar] = useState("");

  async function spoerg(e: FormEvent) {
    e.preventDefault();
    if (!spoergsmaal.trim()) return;
    setSvar("");
    await stream(
      [
        { role: "system", content: "Du er en venlig hjælper for elever i folkeskolen. Svar kort og på dansk." },
        { role: "user", content: spoergsmaal },
      ],
      (bid) => setSvar((s) => s + bid),
    );
  }

  return (
    <section className="kort">
      <h2>
        <Sparkles size={18} /> Spørg AI
      </h2>
      <form onSubmit={spoerg} className="raekke">
        <input value={spoergsmaal} onChange={(e) => setSpoergsmaal(e.target.value)} placeholder="Hvad vil du vide?" />
        <button className="knap" disabled={loading}>
          {loading ? "Tænker…" : "Spørg"}
        </button>
      </form>
      {budget && (
        <p className="fejl">
          AI'en holder pause for denne uge.
          {budget.retryAt && ` Prøv igen ${budget.retryAt.toLocaleDateString("da-DK", { weekday: "long" })}.`}
        </p>
      )}
      {error && <p className="fejl">{error.message}</p>}
      {svar && <p className="svar">{svar}</p>}
    </section>
  );
}

function Klassen() {
  const { members, loading } = useMembers();
  return (
    <section className="kort">
      <h2>
        <Users size={18} /> Hvem har adgang ({members.length})
      </h2>
      {loading ? (
        <p className="daempet">Indlæser…</p>
      ) : (
        <ul className="liste">
          {members.map((m) => (
            <li key={m.id} className="raekke">
              <span className="avatar" style={{ ["--hue" as string]: m.avatarHue }}>
                {m.name.slice(0, 1)}
              </span>
              <span>{m.name}</span>
              <span className="daempet">{[ROLLE[m.role], ...m.classes].join(" · ")}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

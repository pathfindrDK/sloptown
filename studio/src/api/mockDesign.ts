// The design catalog for mock mode: a copy of template-design/catalog.json (the
// studio image is built from studio/ alone, so it can't import the original).
// __tests__/mockDesign.test.ts keeps the two in sync.
import type { DesignCatalog } from "./types";

export const MOCK_DESIGN: DesignCatalog = {
  "styles": [
    {
      "id": "miira",
      "name": {
        "da": "Miira",
        "en": "Miira"
      },
      "description": {
        "da": "Miiras eget udseende",
        "en": "Miira's own look"
      }
    },
    {
      "id": "bootstrap",
      "name": {
        "da": "Bootstrap",
        "en": "Bootstrap"
      },
      "description": {
        "da": "Klassisk og velkendt",
        "en": "Classic and familiar"
      },
      "package": "bootstrap"
    },
    {
      "id": "tailwind",
      "name": {
        "da": "Tailwind",
        "en": "Tailwind"
      },
      "description": {
        "da": "Moderne og fleksibelt",
        "en": "Modern and flexible"
      },
      "package": "tailwindcss"
    },
    {
      "id": "bulma",
      "name": {
        "da": "Bulma",
        "en": "Bulma"
      },
      "description": {
        "da": "Enkelt og venligt",
        "en": "Simple and friendly"
      },
      "package": "bulma"
    },
    {
      "id": "materialize",
      "name": {
        "da": "Material",
        "en": "Material"
      },
      "description": {
        "da": "Googles Material Design",
        "en": "Google's Material Design"
      },
      "package": "@materializecss/materialize"
    }
  ],
  "palettes": [
    {
      "id": "miira",
      "name": {
        "da": "Miira",
        "en": "Miira"
      },
      "light": {
        "primary": "#ffbf00",
        "onPrimary": "#3d2b10",
        "bg": "#fdf8ee",
        "surface": "#fffdf8",
        "text": "#2b2116",
        "muted": "#6e5c44",
        "border": "#e3d5b8"
      },
      "dark": {
        "primary": "#ffc933",
        "onPrimary": "#2b2116",
        "bg": "#211a10",
        "surface": "#2e2416",
        "text": "#f5ecdc",
        "muted": "#c4b393",
        "border": "#4d3c25"
      }
    },
    {
      "id": "hav",
      "name": {
        "da": "Hav",
        "en": "Ocean"
      },
      "light": {
        "primary": "#1f6feb",
        "onPrimary": "#ffffff",
        "bg": "#f3f6fa",
        "surface": "#ffffff",
        "text": "#14202e",
        "muted": "#556273",
        "border": "#d5dde7"
      },
      "dark": {
        "primary": "#5b9bff",
        "onPrimary": "#0b1220",
        "bg": "#0f1620",
        "surface": "#17212d",
        "text": "#e6edf5",
        "muted": "#9aa8b8",
        "border": "#2a3644"
      }
    },
    {
      "id": "skov",
      "name": {
        "da": "Skov",
        "en": "Forest"
      },
      "light": {
        "primary": "#2e7d4f",
        "onPrimary": "#ffffff",
        "bg": "#f4f7f2",
        "surface": "#ffffff",
        "text": "#17241b",
        "muted": "#55665a",
        "border": "#d4dfd4"
      },
      "dark": {
        "primary": "#5cbf85",
        "onPrimary": "#0d1a12",
        "bg": "#111a14",
        "surface": "#18241c",
        "text": "#e5efe7",
        "muted": "#9db3a3",
        "border": "#2b3b30"
      }
    },
    {
      "id": "solnedgang",
      "name": {
        "da": "Solnedgang",
        "en": "Sunset"
      },
      "light": {
        "primary": "#c94f2a",
        "onPrimary": "#ffffff",
        "bg": "#fdf5f1",
        "surface": "#ffffff",
        "text": "#2a1a14",
        "muted": "#725a50",
        "border": "#efd9cf"
      },
      "dark": {
        "primary": "#ff8a66",
        "onPrimary": "#2a1208",
        "bg": "#1f1411",
        "surface": "#2b1c18",
        "text": "#f7e9e3",
        "muted": "#c7a99d",
        "border": "#48302a"
      }
    }
  ]
};

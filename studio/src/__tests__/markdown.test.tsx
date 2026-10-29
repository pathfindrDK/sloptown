import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Markdown } from "../components/Markdown";

afterEach(cleanup);

describe("the AI's Markdown replies", () => {
  it("renders bold, lists and code instead of showing the syntax", () => {
    const { container } = render(
      <Markdown text={"Jeg har rettet **“Venter på forbindelse”**.\n\n- et\n- to\n\nKør `npm test`:\n\n```\nok\n```"} />,
    );
    expect(container.querySelector("strong")?.textContent).toBe("“Venter på forbindelse”");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("p code")?.textContent).toBe("npm test");
    expect(container.querySelector("pre code")?.textContent).toBe("ok\n");
    expect(container.textContent).not.toContain("**");
  });

  it("keeps raw HTML as text, drops javascript: links and opens links in a new tab", () => {
    const { container } = render(
      <Markdown text={'<img src=x onerror="alert(1)"> [klik](javascript:alert(1)) [Miira](https://miira.dk) ![logo](https://x.example/a.png)'} />,
    );
    expect(container.querySelector("img")).toBeNull();
    const links = [...container.querySelectorAll("a")];
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["", "https://miira.dk"]);
    expect(links[1].getAttribute("target")).toBe("_blank");
    expect(links[1].getAttribute("rel")).toContain("noopener");
  });
});

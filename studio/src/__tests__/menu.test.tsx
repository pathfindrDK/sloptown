import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Menu } from "../components/Menu";

afterEach(cleanup);

describe("Menu", () => {
  it("opens outside its (clipping) parent and runs the chosen item", () => {
    const rename = vi.fn();
    const { container } = render(
      <div style={{ overflow: "auto" }}>
        <Menu label="Quiz" trigger="…" items={[{ label: "Omdøb", onSelect: rename }]} />
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Quiz" }));
    const menu = screen.getByRole("menu");
    expect(container.contains(menu)).toBe(false);
    fireEvent.click(screen.getByRole("menuitem", { name: "Omdøb" }));
    expect(rename).toHaveBeenCalled();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("closes on a click elsewhere and on scroll", () => {
    render(<Menu label="Quiz" trigger="…" items={[{ label: "Omdøb", onSelect: () => {} }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Quiz" }));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Quiz" }));
    fireEvent.scroll(window);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

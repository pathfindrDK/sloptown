import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Select } from "../components/Select";

afterEach(cleanup);

const OPTIONS = [
  { value: "owner", label: "Ejer" },
  { value: "editor", label: "Medskaber" },
  { value: "viewer", label: "Bruger" },
] as const;

const renderSelect = (value: (typeof OPTIONS)[number]["value"] = "owner") => {
  const onChange = vi.fn();
  render(<Select label="Se som" value={value} options={[...OPTIONS]} onChange={onChange} />);
  return onChange;
};

describe("Select", () => {
  it("is a Miira listbox, not a native select, and picks by click", () => {
    const onChange = renderSelect();
    expect(document.querySelector("select")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Se som: Ejer" }));
    expect(screen.getByRole("option", { name: "Ejer" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("option", { name: "Bruger" }));
    expect(onChange).toHaveBeenCalledWith("viewer");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("works from the keyboard and gives focus back to the trigger", () => {
    const onChange = renderSelect("editor");
    const trigger = screen.getByRole("button", { name: "Se som: Medskaber" });
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const list = screen.getByRole("listbox");
    expect(document.activeElement).toBe(list);
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("viewer");
    expect(document.activeElement).toBe(trigger);

    fireEvent.keyDown(trigger, { key: "Enter" });
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(onChange).toHaveBeenCalledTimes(1);
  });
});

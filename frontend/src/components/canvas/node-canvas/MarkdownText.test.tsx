import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownText } from "./MarkdownText";

describe("MarkdownText", () => {
  it("renders headings, bold, lists and rules without raw markdown markers", () => {
    const { container } = render(
      <MarkdownText source={"# Title\n\n**logline**: a quarrel\n\n- first\n- second\n\n---\n\nplain line one\nplain line two"} />,
    );

    expect(screen.getByText("Title")).toBeInTheDocument();
    expect(screen.getByText("logline").tagName).toBe("STRONG");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("hr")).not.toBeNull();
    expect(container).not.toHaveTextContent("**");
    expect(container).toHaveTextContent("plain line one");
  });

  it("never turns text into markup", () => {
    const { container } = render(<MarkdownText source={"<img src=x onerror=alert(1)> `<b>`"} />);

    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent("<img src=x onerror=alert(1)>");
  });
});

import { describe, expect, it } from "vitest";
import { summarizeLocatorSpan } from "./locator";

describe("summarizeLocatorSpan", () => {
  it("formats text locators with line span", () => {
    expect(
      summarizeLocatorSpan({ source_type: "txt", line: 1 }, { source_type: "txt", line: 3 }),
    ).toBe("txt · line 1–3");
  });

  it("formats page-only locators", () => {
    expect(summarizeLocatorSpan({ source_type: "pdf", page: 2 }, { source_type: "pdf", page: 2 })).toBe(
      "pdf · p. 2",
    );
  });
});
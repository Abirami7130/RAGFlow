export function summarizeLocator(locator: Record<string, unknown>): string {
  const source = typeof locator.source_type === "string" ? locator.source_type : null;
  const line =
    typeof locator.line === "number"
      ? locator.line
      : typeof locator.line_start === "number"
        ? locator.line_start
        : null;
  const page = typeof locator.page === "number" ? locator.page : null;
  const charStart = typeof locator.char_start === "number" ? locator.char_start : null;
  const parts: string[] = [];
  if (source) {
    parts.push(source);
  }
  if (page !== null) {
    parts.push(`p. ${page}`);
  }
  if (line !== null) {
    parts.push(`line ${line}`);
  }
  if (charStart !== null && page === null && line === null) {
    parts.push(`char ${charStart}`);
  }
  return parts.length ? parts.join(" · ") : "locator";
}

export function summarizeLocatorSpan(start: Record<string, unknown>, end: Record<string, unknown>): string {
  const left = summarizeLocator(start);
  const endLine =
    typeof end.line === "number"
      ? end.line
      : typeof end.line_end === "number"
        ? end.line_end
        : typeof end.line === "number"
          ? end.line
          : null;
  const startLine =
    typeof start.line === "number"
      ? start.line
      : typeof start.line_start === "number"
        ? start.line_start
        : null;
  if (startLine !== null && endLine !== null && endLine !== startLine) {
    return `${left}–${endLine}`;
  }
  const endPage = typeof end.page === "number" ? end.page : null;
  const startPage = typeof start.page === "number" ? start.page : null;
  if (startPage !== null && endPage !== null && endPage !== startPage) {
    return `${left}–p. ${endPage}`;
  }
  return left;
}
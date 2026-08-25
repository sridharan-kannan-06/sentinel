// Formatting shared by every pane.
//
// Durations are the thing this screen is mostly made of, and they are always
// shown at the same width and precision so that a column of them can be scanned
// rather than read.

export function duration(seconds: number): string {
  const overdue = seconds < 0;
  const total = Math.floor(Math.abs(seconds));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;

  let text: string;
  if (days > 0) text = `${days}d ${String(hours).padStart(2, "0")}h`;
  else if (hours > 0)
    text = `${hours}h ${String(minutes).padStart(2, "0")}m`;
  else text = `${minutes}m ${String(secs).padStart(2, "0")}s`;

  return overdue ? `-${text}` : text;
}

export function stamp(iso: string | null | undefined): string {
  if (!iso) return "--";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "--";
  return at.toISOString().replace("T", " ").slice(0, 19) + "Z";
}

export function elapsedSince(iso: string): string {
  return duration((Date.now() - new Date(iso).getTime()) / 1000);
}

// Carbon types its Tag colour as a union rather than a string, so these return
// that union. Colour carries meaning on this screen and is not decorative.
export type TagType =
  | "red"
  | "magenta"
  | "purple"
  | "warm-gray"
  | "teal"
  | "green"
  | "cool-gray"
  | "blue"
  | "gray"
  | "cyan"
  | "high-contrast"
  | "outline";

export function statusTagType(status: string): TagType {
  switch (status) {
    case "BREACHED":
      return "red";
    case "AT_RISK":
      return "magenta";
    case "PENDING_EVIDENCE":
      return "purple";
    case "BLOCKED":
      return "warm-gray";
    case "WAITING_EXTERNAL":
      return "teal";
    case "CLOSED":
      return "green";
    case "CANCELLED":
    case "REJECTED":
      return "cool-gray";
    default:
      return "blue";
  }
}

export function tierTagType(tier: string): TagType {
  switch (tier) {
    case "T3":
      return "red";
    case "T2":
      return "magenta";
    case "T1":
      return "blue";
    default:
      return "gray";
  }
}

export function actionTone(action: string): "bad" | "good" | "neutral" {
  if (
    action.startsWith("failed.") ||
    action.includes("denied") ||
    action.endsWith("failed") ||
    action.includes("rejected") ||
    action.includes("suppressed")
  ) {
    return "bad";
  }
  if (
    action.includes("closed") ||
    action.endsWith("passed") ||
    action.startsWith("acted.") ||
    action.includes("approved")
  ) {
    return "good";
  }
  return "neutral";
}

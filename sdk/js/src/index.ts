export { BudgetExceededError, ROLE_RANK, VibeClient, hasRole } from "./client.js";
export type { ChatMessage, Member, Role, User } from "./client.js";
export { installErrorBridge, isPreview } from "./errors.js";
export type { VibeErrorMessage } from "./errors.js";
export { RequireRole, VibeProvider, miiraTheme, useAi, useCurrentUser, useMembers, useRole } from "./react.js";

export const VERSION = "1.0.0";

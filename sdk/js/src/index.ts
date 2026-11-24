export { BudgetExceededError, ROLE_RANK, SearchError, VibeClient, hasRole } from "./client.js";
export type { ChatMessage, Member, Role, SearchOptions, SearchResult, User } from "./client.js";
export { announceReady, installErrorBridge, isPreview } from "./errors.js";
export type { VibeErrorMessage } from "./errors.js";
export { RequireRole, VibeProvider, miiraTheme, useAi, useCurrentUser, useMembers, useRole, useSearch } from "./react.js";

export const VERSION = "1.1.0";

import type { StaffMember, StaffRole } from "../../lib/staff";
import type { TaskItem } from "../../types";

export interface RoleTaskConfirmation { digest: string; round: number; instance: string }
export type RoleTask = TaskItem & { confirmation: RoleTaskConfirmation; created: string; updated: string; execution?: unknown;
  collaboration: NonNullable<TaskItem['collaboration']> & { execution?: unknown; dispatch: NonNullable<NonNullable<TaskItem['collaboration']>['dispatch']> & {
    member: string; role: string; delivery: "review" | "notify"; routine?: unknown;
  } } };
export interface RoleConversationSnapshot {
  version: 1; instance: string; identity_digest: string; role_digest: string;
  member: StaffMember; role: StaffRole; tasks: RoleTask[];
  history_complete: boolean; next_cursor: string | null; submitted_task: RoleTask | null;
  can_dispatch: boolean; unavailable_reason: string;
}
export interface RoleMessage {
  request_id: string; prompt: string; acceptance: string[]; delivery: "review" | "notify";
  member_revision: number; role_revision: number; instance: string; identity_digest: string;
}
export type RoleVerdict = "accept" | "rework" | "stop";
export type RoleTaskDecision = RoleTaskConfirmation & { verdict: RoleVerdict; note: string };
export interface MemberProposalInput {
  request_id: string; name: string; role: string; role_revision: number; role_digest: string;
  engine: string; model: string; instance: string;
}
export interface RoleConversationClient {
  readRoleConversation(member: string, before?: string, requestId?: string): Promise<RoleConversationSnapshot>;
  sendRoleMessage(member: string, input: RoleMessage): Promise<{ task: RoleTask; replayed: boolean }>;
  decideRoleTask(member: string, task: string, input: RoleTaskDecision): Promise<{ task: RoleTask; stop_requested?: boolean }>;
}

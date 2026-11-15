import { Plus, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import type { AssistantAvatar } from "../types";
import type { StaffSnapshot } from "../lib/staff";
import { COPY } from "../lib/copy.generated";
import { RoleAvatar, staffCharacter } from "./RoleAvatar";
import { IconButton } from "./ui";
import "./RoleProfile.css";

/** An existing registry projection, not another roster or permission source. */
export function RoleNavigation({ name, avatar, directory, error, connected, onAssistant, onMember, onAdd, onRefresh, currentMember, currentAssistant }: {
  name: string; avatar?: AssistantAvatar | null; directory: StaffSnapshot | null; error?: string;
  connected: boolean; onAssistant: () => void; onMember: (id: string) => void;
  onAdd: () => void; onRefresh?: () => void;
  currentMember?: string; currentAssistant?: boolean;
}) {
  const status: Record<string, string> = { active: COPY.role.active, paused: COPY.role.paused, retired: COPY.role.retired };
  return <section className="vc-role-navigation" aria-label={COPY.role.list}>
    <div className="vc-role-navigation-heading"><h3>{COPY.role.list}</h3>
      {onRefresh && <IconButton label={COPY.role.refresh} icon={<RefreshCw aria-hidden="true" />} onClick={onRefresh} />}</div>
    <RoleRow name={name} subtitle={COPY.characters.general} current={currentAssistant} avatar={<RoleAvatar character="general" avatar={avatar} size="small" />} onOpen={onAssistant} />
    {directory?.members.filter(member => member.status !== "retired").map(member => {
      const role = directory.roles.find(role => role.id === member.role);
      const checked = connected && !error;
      return <RoleRow key={member.id} name={member.name} avatar={<RoleAvatar character={staffCharacter(role)} size="small" />}
        subtitle={checked ? `${role?.title === member.name ? role.brief : role?.title || COPY.role.unknown}${member.status === "active" ? member.busy ? ` · ${COPY.role.busy}` : "" : ` · ${status[member.status] || COPY.role.unknown}`}` : COPY.role.unverified}
        current={currentMember === member.id} onOpen={() => onMember(member.id)} />;
    })}
    {error && <p className="vc-role-navigation-note" role="status">{error}</p>}
    <button type="button" className="vc-role-add" disabled={!connected} onClick={onAdd}><Plus aria-hidden="true" />{COPY.role.add}</button>
  </section>;
}

function RoleRow({ name, subtitle, avatar, onOpen, current }: { name: string; subtitle: string; avatar: ReactNode; onOpen: () => void; current?: boolean }) {
  return <button type="button" className={`vc-role-row${current ? " is-current" : ""}`} aria-current={current ? "page" : undefined} aria-label={COPY.role.open_conversation.replace("{name}", name)} onClick={onOpen}>
    {avatar}<span><strong>{name}</strong><small title={subtitle}>{subtitle}</small></span>
  </button>;
}

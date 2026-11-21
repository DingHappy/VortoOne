import { keys, type AgentMemberPolicyChange, type MemberAutoApproval, useApi } from "@agenteschat/client";
import { errorText } from "@agenteschat/client/lib/errors";
import { Button, Checkbox, Dialog, DialogBody, DialogFooter, Switch } from "@agenteschat/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useOrg } from "@/components/org-context";
import { useToast } from "@/lib/toast";

export function MemberPolicy({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const { org } = useOrg();
  const api = useApi();
  const queryClient = useQueryClient();
  const toast = useToast();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<AgentMemberPolicyChange>();
  const queryKey = keys.agentMemberPolicy(org.id, agentId);
  const policy = useQuery({
    queryKey,
    queryFn: () => api.agents.memberPolicy(org.id, agentId),
    enabled: open,
    retry: false,
    staleTime: 0,
  });
  const save = useMutation({
    meta: { silent: true },
    mutationFn: (change: AgentMemberPolicyChange) => api.agents.setMemberPolicy(org.id, agentId, change),
    onSuccess: (current) => {
      queryClient.setQueryData(queryKey, current);
      void queryClient.invalidateQueries({ queryKey: keys.agents(org.id) });
      setOpen(false);
      toast.show(t("common.saved"));
    },
  });
  const current = !policy.isFetching && !policy.isError ? policy.data : undefined;
  const activeDraft = draft?.roleVersion === current?.roleVersion ? draft : undefined;
  const actForMembers = activeDraft?.actForMembers ?? current?.actForMembers;
  const autoApprove = activeDraft?.autoApprove ?? current?.autoApprove ?? [];
  const changedDelegation = current && actForMembers !== current.actForMembers;
  const changedApproval = current && (
    autoApprove.length !== current.autoApprove.length ||
    autoApprove.some((kind) => !current.autoApprove.includes(kind))
  );
  const title = t("agentPage.memberPolicy.title");

  function toggleApproval(kind: MemberAutoApproval, on: boolean) {
    if (!current) return;
    save.reset();
    setDraft({
      ...activeDraft,
      roleVersion: current.roleVersion,
      autoApprove: on ? [...autoApprove, kind] : autoApprove.filter((item) => item !== kind),
    });
  }

  return (
    <div>
      <Button size="sm" icon="shield" onClick={() => {
        setDraft(undefined);
        save.reset();
        setOpen(true);
      }}>
        {title}
      </Button>
      <Dialog open={open} onOpenChange={(next) => {
        if (!save.isPending) setOpen(next);
      }} title={title} closeLabel={t("common.close")} width={480}>
        <DialogBody>
          <div className="ac-form">
            {policy.isFetching && <p role="status">{t("common.loading")}</p>}
            {policy.isError && (
              <div className="stack-4">
                <p role="alert">{errorText(t, policy.error)}</p>
                <Button size="sm" onClick={() => void policy.refetch()}>{t("common.retry")}</Button>
              </div>
            )}
            {current && (
              <>
                <label htmlFor={id}>
                  <Switch id={id} checked={actForMembers === true} disabled={save.isPending} onCheckedChange={(on) => {
                    save.reset();
                    setDraft({ ...activeDraft, roleVersion: current.roleVersion, actForMembers: on });
                  }} />
                  {" "}{t("agentPage.memberPolicy.delegation")}
                </label>
                <p>{t("agentPage.memberPolicy.delegationHelp")}</p>
                {current.canSetAutoApprove && (
                  <div role="group" aria-labelledby={`${id}-approval`} className="stack-4">
                    <strong id={`${id}-approval`}>{t("agentPage.memberPolicy.approval")}</strong>
                    <p>{t(current.availableAutoApprove.length
                      ? "agentPage.memberPolicy.approvalHelp" : "agentPage.memberPolicy.noEligibleTools")}</p>
                    {current.availableAutoApprove.map((kind) => (
                      <label key={kind} htmlFor={`${id}-${kind}`}>
                        <Checkbox id={`${id}-${kind}`} checked={autoApprove.includes(kind)}
                          disabled={save.isPending} onCheckedChange={(on) => toggleApproval(kind, on === true)} />
                        {" "}{t(`agentPage.memberPolicy.${kind}`)}
                      </label>
                    ))}
                  </div>
                )}
              </>
            )}
            {save.isError && <p role="alert">{errorText(t, save.error)}</p>}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button onClick={() => setOpen(false)} disabled={save.isPending}>{t("common.cancel")}</Button>
          <Button variant="primary" loading={save.isPending} disabled={!changedDelegation && !changedApproval}
            onClick={() => {
              if (current && (changedDelegation || changedApproval)) {
                save.mutate({
                  roleVersion: current.roleVersion,
                  ...(changedDelegation ? { actForMembers } : {}),
                  ...(changedApproval ? { autoApprove } : {}),
                });
              }
            }}>
            {t("common.save")}
          </Button>
        </DialogFooter>
      </Dialog>
    </div>
  );
}

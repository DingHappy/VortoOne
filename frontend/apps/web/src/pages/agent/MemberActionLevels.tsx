import {
  keys,
  ORG_ROLE_ACT_LEVELS,
  type OrgRoleActLevel,
  useApi,
} from "@agenteschat/client";
import { errorText } from "@agenteschat/client/lib/errors";
import {
  Button,
  Dialog,
  DialogBody,
  DialogFooter,
  Field,
  Select,
} from "@agenteschat/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useOrg } from "@/components/org-context";
import { useToast } from "@/lib/toast";

type Choice = "inherit" | OrgRoleActLevel;

export function MemberActionLevels({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const { org } = useOrg();
  const api = useApi();
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [userId, setUserId] = useState("");
  const [draft, setDraft] = useState<Choice>();
  const queryKey = keys.agentMemberActionLevels(org.id, agentId);
  const levels = useQuery({
    queryKey,
    queryFn: () => api.agents.memberActionLevels(org.id, agentId),
    enabled: open,
    retry: false,
  });
  const save = useMutation({
    meta: { silent: true },
    mutationFn: ({
      userId,
      level,
    }: {
      userId: string;
      level: OrgRoleActLevel | null;
    }) => api.agents.setMemberActionLevel(org.id, agentId, userId, level),
    onSuccess: () => {
      setOpen(false);
      toast.show(t("common.saved"));
      void qc.invalidateQueries({ queryKey: keys.agents(org.id) });
      void qc.invalidateQueries({ queryKey: keys.dms(org.id) });
    },
    onSettled: () => void qc.invalidateQueries({ queryKey }),
  });
  const data = !levels.isError && !levels.isFetching ? levels.data : undefined;
  const target = data?.members.find((row) => row.userId === userId);
  const ceiling = data ? ORG_ROLE_ACT_LEVELS.indexOf(data.maxLevel) : -1;
  const canSet = (level: OrgRoleActLevel) =>
    ORG_ROLE_ACT_LEVELS.indexOf(level) <= ceiling;
  const changed =
    target && draft !== undefined && draft !== (target.level ?? "inherit");
  const requestLevel = draft === "inherit" ? target?.roleLevel : draft;
  const canSave = changed && requestLevel !== undefined && canSet(requestLevel);
  const title = t("agentPage.memberActionLevels.title");

  return (
    <div>
      <Button
        size="sm"
        icon="members"
        onClick={() => {
          setUserId("");
          setDraft(undefined);
          save.reset();
          setOpen(true);
        }}
      >
        {title}
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!save.isPending) setOpen(next);
        }}
        title={title}
        description={t("agentPage.memberActionLevels.description")}
        closeLabel={t("common.close")}
        width={480}
      >
        <DialogBody>
          <div className="ac-form">
            {levels.isFetching && <p role="status">{t("common.loading")}</p>}
            {levels.isError && (
              <div className="stack-4">
                <p role="alert">{errorText(t, levels.error)}</p>
                <Button size="sm" onClick={() => void levels.refetch()}>
                  {t("common.retry")}
                </Button>
              </div>
            )}
            {data &&
              (data.members.length ? (
                <Field label={t("agentPage.memberActionLevels.member")}>
                  <Select
                    value={userId || undefined}
                    placeholder={t("agentPage.memberActionLevels.chooseMember")}
                    options={data.members.map(({ userId, name }) => ({
                      value: userId,
                      label: name,
                    }))}
                    disabled={save.isPending}
                    onValueChange={(next) => {
                      setUserId(next);
                      setDraft(undefined);
                      save.reset();
                    }}
                  />
                </Field>
              ) : (
                <p>{t("agentPage.memberActionLevels.empty")}</p>
              ))}
            {target && (
              <Field
                label={t("agentPage.memberActionLevels.level")}
                example={
                  !target.inAudience &&
                  t("agentPage.memberActionLevels.outsideAudience")
                }
                error={save.isError ? errorText(t, save.error) : undefined}
              >
                <Select<Choice>
                  value={draft ?? target.level ?? "inherit"}
                  options={[
                    {
                      value: "inherit",
                      label: t("agentPage.memberActionLevels.inherit", {
                        level: t(
                          `rolesPage.actLevel.options.${target.roleLevel}`,
                        ),
                      }),
                      disabled: !canSet(target.roleLevel),
                    },
                    ...ORG_ROLE_ACT_LEVELS.map((value) => ({
                      value,
                      label: t(`rolesPage.actLevel.options.${value}`),
                      disabled: !canSet(value),
                    })),
                  ]}
                  disabled={save.isPending}
                  onValueChange={(next) => {
                    setDraft(next);
                    save.reset();
                  }}
                />
              </Field>
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button onClick={() => setOpen(false)} disabled={save.isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={!canSave}
            onClick={() => {
              if (canSave && draft !== undefined)
                save.mutate({
                  userId,
                  level: draft === "inherit" ? null : draft,
                });
            }}
          >
            {t("common.save")}
          </Button>
        </DialogFooter>
      </Dialog>
    </div>
  );
}

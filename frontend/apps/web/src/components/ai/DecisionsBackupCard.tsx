import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  CardHeader,
  Chip,
  Dialog,
  DialogBody,
  DialogFooter,
  Field,
  Input,
} from "@vortoone/ui";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useApi } from "@/api/context";
import { ErrorState, LoadingLine } from "@/components/Loading";

type BackupAction = "set" | "disable";

const validKey = (key: string) => /^[!-~]{1,400}$/.test(key);

/** The workspace owner can store one project key used only by the recommendation fallback. */
export function DecisionsBackupCard({ orgId }: { orgId: string }) {
  const { t } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const queryKey = ["decisionsBackup", orgId] as const;
  const status = useQuery({
    queryKey,
    queryFn: async () => ({
      configured: (await api.agents.decisionsBackup(orgId)).configured,
    }),
  });
  const [dialog, setDialog] = useState<BackupAction | null>(null);
  const [keyState, setKeyState] = useState<"empty" | "valid" | "invalid">(
    "empty",
  );
  const keyInput = useRef<HTMLInputElement>(null);
  const update = useMutation({
    mutationFn: async (action: BackupAction) => {
      // Keep the new key in the password field and the request, out of shared mutation variables.
      const apiKey =
        action === "set" ? (keyInput.current?.value.trim() ?? "") : null;
      if (apiKey !== null && !validKey(apiKey))
        throw new Error("Invalid Decisions project key");
      const { configured } = await api.agents.setDecisionsBackup(orgId, {
        apiKey,
      });
      return { configured };
    },
    meta: { silent: true },
    networkMode: "always",
    retry: false,
    gcTime: 0,
    onSuccess: async (result) => {
      if (keyInput.current) keyInput.current.value = "";
      setKeyState("empty");
      setDialog(null);
      await queryClient.cancelQueries({ queryKey });
      queryClient.setQueryData(queryKey, result);
    },
  });

  function openDialog(next: BackupAction | null) {
    if (update.isPending) return;
    if (keyInput.current) keyInput.current.value = "";
    setKeyState("empty");
    update.reset();
    setDialog(next);
  }

  const configured = status.data?.configured;
  const keyActionLabel = t(configured ? "settings.rotate" : "settings.addKey");
  const disableLabel = t("settings.decisionsBackup.disable");
  const saveFailed = update.isError && (
    <Alert>{t("common.unknownError")}</Alert>
  );

  return (
    <Card aria-labelledby="decisions-backup-title">
      <CardHeader
        id="decisions-backup-title"
        mark="openai"
        title={t("settings.decisionsBackup.title")}
        subtitle={t("settings.decisionsBackup.description")}
      />
      {status.isPending && <LoadingLine width={96} />}
      {status.isError && !status.data && (
        <ErrorState onRetry={() => void status.refetch()} />
      )}
      {configured !== undefined && (
        <div className="ac-actline">
          <Chip tone={configured ? "done" : "neutral"} role="status">
            {t(
              configured
                ? "settings.decisionsBackup.enabled"
                : "settings.decisionsBackup.disabled",
            )}
          </Chip>
          <div className="ac-acts">
            <Button
              size="sm"
              onClick={() => openDialog("set")}
              disabled={update.isPending}
            >
              {keyActionLabel}
            </Button>
            {configured && (
              <Button
                size="sm"
                onClick={() => openDialog("disable")}
                disabled={update.isPending}
              >
                {disableLabel}
              </Button>
            )}
          </div>
        </div>
      )}
      <Dialog
        open={dialog === "set"}
        onOpenChange={(open) => !open && openDialog(null)}
        title={keyActionLabel}
        closeLabel={t("common.close")}
        width={440}
      >
        {dialog === "set" && (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (keyState === "valid" && !update.isPending)
                update.mutate("set");
            }}
          >
            <DialogBody>
              <Field
                label={t("settings.decisionsBackup.projectKey")}
                error={
                  keyState === "invalid"
                    ? t("settings.decisionsBackup.invalidKey")
                    : undefined
                }
              >
                <Input
                  ref={keyInput}
                  type="password"
                  autoComplete="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  disabled={update.isPending}
                  onChange={(event) => {
                    const value = event.currentTarget.value.trim();
                    setKeyState(
                      !value ? "empty" : validKey(value) ? "valid" : "invalid",
                    );
                  }}
                />
              </Field>
              {saveFailed}
            </DialogBody>
            <DialogFooter>
              <Button
                disabled={update.isPending}
                onClick={() => openDialog(null)}
              >
                {t("common.cancel")}
              </Button>
              <Button
                type="submit"
                variant="primary"
                disabled={keyState !== "valid"}
                loading={update.isPending}
              >
                {t("settings.saveKey")}
              </Button>
            </DialogFooter>
          </form>
        )}
      </Dialog>
      <Dialog
        open={dialog === "disable"}
        onOpenChange={(open) => !open && openDialog(null)}
        title={disableLabel}
        description={t("settings.decisionsBackup.disableDescription")}
        closeLabel={t("common.close")}
        width={440}
      >
        {saveFailed && <DialogBody>{saveFailed}</DialogBody>}
        <DialogFooter>
          <Button disabled={update.isPending} onClick={() => openDialog(null)}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="danger"
            loading={update.isPending}
            onClick={() => update.mutate("disable")}
          >
            {disableLabel}
          </Button>
        </DialogFooter>
      </Dialog>
    </Card>
  );
}

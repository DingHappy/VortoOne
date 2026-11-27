import { keys, type Credential, useAddCredential } from "@vortoone/client";
import { endpointAddressLooksValid } from "@vortoone/client/lib/endpoints";
import { errorText } from "@vortoone/client/lib/errors";
import { keyThinks } from "@vortoone/client/lib/providers";
import { Button, Dialog, DialogBody, DialogFooter, Field, Input, Select } from "@vortoone/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useApi } from "@/api/context";

const providers = {
  openai: { name: "OpenAI", address: "https://api.openai.com/v1", model: "" },
  deepseek: { name: "DeepSeek", address: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  custom: { name: "", address: "", model: "" },
};
type Provider = keyof typeof providers;

export function OnboardingModelDialog({
  orgId,
  onConnected,
  onClose,
}: {
  orgId: string;
  onConnected?: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const id = useId();
  const [provider, setProvider] = useState<Provider>("openai");
  const [address, setAddress] = useState(providers.openai.address);
  const [model, setModel] = useState(providers.openai.model);
  const [apiKey, setApiKey] = useState("");
  const [tested, setTested] = useState<Credential>();
  const [problem, setProblem] = useState<"unverified" | "inactive">();
  const add = useAddCredential(orgId, true);
  const apply = useMutation({
    meta: { silent: true },
    mutationFn: async () => {
      let status = await api.agents.aiStatus(orgId);
      if (status.apiKey) return { status, changed: false };

      const key = tested ?? (await add.mutateAsync({
        provider: "custom",
        label: providers[provider].name || t("onboarding.flow.ai.model.custom"),
        apiKey: apiKey.trim(),
        endpoint: { style: "openai", baseURL: address.trim(), models: [{ id: model.trim() }] },
      }));
      if (!keyThinks(key)) {
        setProblem("unverified");
        return null;
      }
      setTested(key);
      setApiKey("");

      // Another session may have connected a model while this one's test was running.
      status = await api.agents.aiStatus(orgId);
      if (status.apiKey) return { status, changed: false };
      await api.agents.makeDefaultCredential(orgId, key.id);
      status = await api.agents.aiStatus(orgId);
      if (!status.apiKey) {
        setProblem("inactive");
        return null;
      }
      return { status, changed: true };
    },
    onSuccess: (result) => {
      if (!result) return;
      qc.setQueryData(keys.aiStatus(orgId), result.status);
      void qc.invalidateQueries({ queryKey: keys.settings(orgId) });
      if (result.changed) onConnected?.();
      onClose();
    },
  });
  const clearError = () => {
    setProblem(undefined);
    apply.reset();
  };
  const selectProvider = (next: Provider) => {
    setProvider(next);
    setAddress(providers[next].address);
    setModel(providers[next].model);
    setApiKey("");
    clearError();
  };
  const busy = apply.isPending;
  const valid = tested !== undefined || (endpointAddressLooksValid(address) && !!model.trim() && !!apiKey.trim());
  const error = problem ? t(`onboarding.flow.ai.model.${problem}`) : apply.isError ? errorText(t, apply.error) : null;

  return (
    <Dialog
      open
      onOpenChange={(open) => { if (!open && !busy) onClose(); }}
      title={t("onboarding.flow.ai.model.title")}
      description={t("onboarding.flow.ai.model.description")}
      closeLabel={t("common.close")}
    >
      <form
        style={{ display: "contents" }}
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy && valid) {
            clearError();
            apply.mutate();
          }
        }}
      >
        <DialogBody>
          <Field id={`${id}-provider`} label={t("onboarding.flow.ai.model.provider")}>
            <Select
              value={provider}
              onValueChange={selectProvider}
              options={[
                { value: "openai", label: providers.openai.name },
                { value: "deepseek", label: providers.deepseek.name },
                { value: "custom", label: t("onboarding.flow.ai.model.custom") },
              ]}
              disabled={busy || tested !== undefined}
            />
          </Field>
          <Field id={`${id}-address`} label={t("endpoint.address")}>
            <Input
              type="url"
              required
              value={address}
              onChange={(event) => { setAddress(event.target.value); clearError(); }}
              placeholder="https://example.com/v1"
              maxLength={300}
              disabled={busy || tested !== undefined}
              autoComplete="off"
              spellCheck={false}
            />
          </Field>
          <Field id={`${id}-model`} label={t("onboarding.flow.ai.model.id")}>
            <Input
              required
              value={model}
              onChange={(event) => { setModel(event.target.value); clearError(); }}
              placeholder={t("endpoint.modelPlaceholder")}
              maxLength={160}
              disabled={busy || tested !== undefined}
              autoComplete="off"
              spellCheck={false}
            />
          </Field>
          {tested === undefined && (
            <Field id={`${id}-key`} label={t("endpoint.key")}>
              <Input
                type="password"
                required
                value={apiKey}
                onChange={(event) => { setApiKey(event.target.value); clearError(); }}
                maxLength={400}
                disabled={busy}
                autoComplete="off"
                spellCheck={false}
              />
            </Field>
          )}
          {error && <p className="ac-field__error" role="alert">{error}</p>}
        </DialogBody>
        <DialogFooter>
          <Button disabled={busy} onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" variant="primary" loading={busy} disabled={busy || !valid}>
            {t(tested ? "onboarding.flow.ai.model.retry" : "onboarding.flow.ai.model.connect")}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}

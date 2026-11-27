import { useTranslation } from "react-i18next";

export function OnboardingProgress({ done, total, current }: { done: number; total: number; current: number }) {
  const { t } = useTranslation();
  const count = t("agentSetup.count", { done, total });
  return (
    <div className="onb__progress">
      <div className="onb__progress-text" aria-hidden="true">
        <span>{done === total ? t("onboarding.progress.done") : t("onboarding.progress.left", { count: total - done })}</span>
        <span>{count}</span>
      </div>
      <div
        className="onb__progress-track"
        role="progressbar"
        aria-label={t("onboarding.stepsLabel")}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        aria-valuetext={count}
      >
        {Array.from({ length: total }, (_, index) => (
          <span key={index} data-done={index < done || undefined} data-current={index === current || undefined} />
        ))}
      </div>
    </div>
  );
}

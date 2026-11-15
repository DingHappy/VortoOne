import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import type { DesktopLlmProfileStatus } from "../../types";
import { ModelServices } from "./ModelServices";

it.each([null, { configured: true, baseUrl: "", model: "sample", provider: "custom", requiresKey: true } as DesktopLlmProfileStatus])
  ("retains one explicit Connect AI entry and separate membership access after grouping the service list: %s", profile => {
    const onProfile = vi.fn(), onRestartRuntime = vi.fn(async () => true);
    const html = renderToStaticMarkup(<ModelServices profile={profile} onProfile={onProfile} onRestartRuntime={onRestartRuntime}
      editingDefault={false} onEditDefault={vi.fn()} onOpenAccount={vi.fn()} />);
    expect(html.match(/连接 AI/g)).toHaveLength(1);
    expect(html).toContain("VortoOne 会员");
    expect(html).toContain('aria-haspopup="dialog"');
    expect(html).not.toContain('role="group" aria-label="选择要添加的服务"');
    expect(onProfile).not.toHaveBeenCalled();
    expect(onRestartRuntime).not.toHaveBeenCalled();
  });

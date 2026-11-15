import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { PersonalRolesFixture } from "../../../qa/personal-roles";
import { GatewayClient } from "../../gateway";
import { COPY } from "../../lib/copy.generated";

it("keeps channels and their creation entry visible next to roles before the preview has loaded records", () => {
  const create = vi.spyOn(GatewayClient.prototype, "createChannel");
  try {
    const html = renderToStaticMarkup(<PersonalRolesFixture />);
    expect(html).toContain(`aria-label="${COPY.nav.channels}"`);
    expect(html).toContain(`aria-label="${COPY.channel.create}"`);
    expect(html).toContain('aria-label="角色"');
    expect(create).not.toHaveBeenCalled();
  } finally { create.mockRestore(); }
});

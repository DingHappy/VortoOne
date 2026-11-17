import { instanceQuery } from "@agenteschat/client";
import { PUBLIC_SOURCE_URL } from "@agenteschat/client/lib/about";
import { shownAddress } from "@agenteschat/client/lib/sites";
import { Card, CardHeader, Icon } from "@agenteschat/ui";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useApi } from "@/api/context";
import { LoadingLine } from "@/components/Loading";
import { PrefRow } from "@/components/NoticePrefs";

/** An outside page in a new tab, in the house link style. */
function Out({ href, children }: { href: string; children?: ReactNode }) {
  return (
    <a className="ac-link" href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

/** Version and optional source information for this installation. */
export function AboutSection() {
  const { t } = useTranslation();
  const instance = useQuery(instanceQuery(useApi()));
  const source = instance.data?.sourceUrl ?? PUBLIC_SOURCE_URL;
  return (
    <Card aria-labelledby="about-made">
      <CardHeader
        id="about-made"
        icon="info"
        title={
          // One inline run: the heading lays its children out with a gap, which would part the link from its words.
          <span>
            VortoOne
          </span>
        }
      />
      <div className="settings-rows">
        <PrefRow
          id="about-version"
          icon="download"
          label={t("settings.about.version")}
          control={instance.data ? <span className="ac-num">{instance.data.version}</span> : <LoadingLine width={80} />}
        />
        {source && (
          <PrefRow
            id="about-source"
            icon="code"
            label={t("settings.about.source")}
            control={
              <Out href={source}>
                <span className="ac-iconline">
                  {shownAddress(source)}
                  <Icon name="external" size={14} />
                </span>
              </Out>
            }
          />
        )}
      </div>
    </Card>
  );
}

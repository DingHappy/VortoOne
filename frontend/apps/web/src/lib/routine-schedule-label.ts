import type { TFunction } from "i18next";

export const weekdayName = (day: string, locale: string) =>
  new Intl.DateTimeFormat(locale, { weekday: "long", timeZone: "UTC" }).format(
    new Date(Date.UTC(2024, 0, 7 + Number(day))),
  );

export function describeRoutineSchedule(
  schedule: string,
  t: TFunction,
  locale: string,
) {
  const daily = /^at (\d{2}:\d{2})$/.exec(schedule);
  if (daily) return t("routines.memberProposals.dailyAt", { time: daily[1] });
  const [, minute, hour, day] =
    /^(\d{1,2}) (\d{1,2}) \* \* ([0-6])$/.exec(schedule) ?? [];
  if (minute && hour && day) {
    return t("routines.memberProposals.weeklyAt", {
      day: weekdayName(day, locale),
      time: `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`,
    });
  }
  return schedule;
}

/** All IANA time zones supported by the runtime, with friendlier labels for
 * the three Indonesian zones. Used by the Settings timezone picker. */
export function listTimezones(): { value: string; label: string }[] {
  if (typeof Intl === "undefined" || !Intl.supportedValuesOf) {
    return [];
  }
  const zones = Intl.supportedValuesOf("timeZone") as string[];
  return zones.map((zone) => {
    const idZone = zone === "Asia/Jakarta" ? "Asia/Jakarta (WIB, UTC+7)" : zone === "Asia/Makassar" ? "Asia/Makassar (WITA, UTC+8)" : zone === "Asia/Jayapura" ? "Asia/Jayapura (WIT, UTC+9)" : zone;
    return { value: zone, label: idZone };
  });
}

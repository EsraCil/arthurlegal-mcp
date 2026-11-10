#!/usr/bin/env bash
# Fly'a Dockerfile.code ile dağıtır (.github/workflows/fly-dagitim.yml). Ortam: SHA, UYGULAMA, FLY_API_TOKEN, DERLEYICI,
# ETIKET (iş akışı verir: gh-<commit>, veri dalında ek varsa gh-<commit>-ek-<veri>; yoksa gh-<commit>).
#
#   DERLEYICI=fly     Önce Fly'ın derleyicisi. Derleme aşamasında düşerse (çoğu kez derleyici makinesi uykudan geç
#                     uyanır, 2026-11-07) 60 sn sonra bir kez daha; yine düşerse imaj bu makinede derlenir.
#   DERLEYICI=github  İmaj doğrudan bu makinede derlenir: taban imaj Fly'ın kayıt deposundan çekilir, yalnız yeni
#                     katmanlar geri gönderilir.
#
# Sürüm ya da sağlık denetimi aşamasında düşen dağıtım yeniden denenmez: iş akışı önceki imaja döner.
set -uo pipefail

etiket="${ETIKET:-gh-${SHA::7}}"
ortak=(. -a "$UYGULAMA" --config fly.toml --dockerfile Dockerfile.code --image-label "$etiket" --wait-timeout 10m)
gunluk="${RUNNER_TEMP:-/tmp}/dagitim.log"

derleme_hatasi() {
  grep -qE "failed to fetch an image or build from source|^Error.*(remote builder|builder machine)" "$gunluk"
}

if [ "${DERLEYICI:-fly}" = fly ]; then
  for deneme in 1 2; do
    flyctl deploy "${ortak[@]}" --depot=false --remote-only 2>&1 | tee "$gunluk"
    rc=${PIPESTATUS[0]}
    [ "$rc" -eq 0 ] && exit 0
    derleme_hatasi || exit "$rc"
    echo "::warning::Fly derleyicisi $deneme. denemede derleme aşamasında düştü."
    [ "$deneme" -eq 1 ] && sleep 60
  done
  echo "::warning::Fly derleyicisi açılmadı; imaj bu makinede derleniyor."
fi

flyctl auth docker
flyctl deploy "${ortak[@]}" --local-only

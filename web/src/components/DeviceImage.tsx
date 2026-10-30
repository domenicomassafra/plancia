import { useState, type ReactNode } from "react";

type DeviceImageProps = {
  image?: string | null;
  className?: string;
  fallback?: ReactNode;
  loading?: "lazy" | "eager";
};

/**
 * One canonical device-asset surface.
 *
 * A device image that is absent *or* fails to load must never render a broken
 * `<img>`: the explicit `fallback` is shown instead, so a missing release asset
 * degrades to the same neutral mark as a host without an image. Failure is
 * tracked per exact asset name, so reusing the surface for another host starts
 * clean instead of inheriting the previous host's failure.
 */
export function DeviceImage({
  image,
  className,
  fallback = null,
  loading,
}: DeviceImageProps) {
  const [failedImage, setFailedImage] = useState<string | null>(null);
  if (!image || failedImage === image) return <>{fallback}</>;
  return (
    <img
      src={`/assets/devices/${image}`}
      alt=""
      aria-hidden="true"
      className={className}
      draggable={false}
      loading={loading}
      onError={() => setFailedImage(image ?? null)}
    />
  );
}

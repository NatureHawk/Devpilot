import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Fail the production build on type errors rather than shipping them.
  typescript: { ignoreBuildErrors: false },
};

export default nextConfig;

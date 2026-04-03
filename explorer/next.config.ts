import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async redirects() {
    return [
      {
        source: '/transactions',
        destination: '/txs',
        permanent: true,
      },
      {
        source: '/transactions/:hash',
        destination: '/tx/:hash',
        permanent: true,
      },
    ];
  },
};

export default nextConfig;

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // Minify with terser for better obfuscation
    minify: 'terser',
    terserOptions: {
      compress: {
        // Remove console.log in production
        drop_console: true,
        drop_debugger: true,
        // Dead code removal
        dead_code: true,
        // Aggressive optimizations
        passes: 2,
      },
      mangle: {
        // Mangle variable names for obfuscation
        toplevel: true,
        properties: {
          // Only mangle internal properties (safe)
          regex: /^_/,
        },
      },
      format: {
        // Remove all comments
        comments: false,
      },
    },
    // Generate hashed filenames (cache-busting + harder to guess)
    rollupOptions: {
      output: {
        // Use hashed chunk names
        chunkFileNames: 'assets/[hash].js',
        entryFileNames: 'assets/[hash].js',
        assetFileNames: 'assets/[hash].[ext]',
      },
    },
    // Disable source maps in production
    sourcemap: false,
  },
})

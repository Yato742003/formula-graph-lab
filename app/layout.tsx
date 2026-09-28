import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import '@xyflow/react/dist/style.css';
import 'katex/dist/katex.min.css';
import './globals.css';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

export const metadata: Metadata = {
  title: 'FormulaGraph Lab',
  description:
    'A temporal research graph for extracting, connecting, and validating equations from HTML papers.',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`} suppressHydrationWarning>
      <body className="antialiased" suppressHydrationWarning>
        {/* Suppress browser-extension hydration mismatches (fdprocessedid) and benign ResizeObserver loop notifications before React & Vinext load */}
        <script dangerouslySetInnerHTML={{ __html: `(function(){var o=console.error;console.error=function(){for(var i=0;i<arguments.length;i++){if(typeof arguments[i]==='string'&&arguments[i].indexOf('fdprocessedid')!==-1)return}o.apply(console,arguments)};window.addEventListener('error',function(e){if(e&&e.message&&(e.message.indexOf('ResizeObserver loop completed with undelivered notifications')!==-1||e.message.indexOf('ResizeObserver loop limit exceeded')!==-1)){e.stopImmediatePropagation();e.preventDefault()}},true)})()` }} />
        {children}
      </body>
    </html>
  );
}

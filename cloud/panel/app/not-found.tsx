import Link from "next/link";

export default function NotFound() {
  return (
    <main className="boot">
      <h1>Sahifa topilmadi</h1>
      <p className="muted">Manzil noto&apos;g&apos;ri yoki sahifa o&apos;chirilgan.</p>
      <Link className="btn primary" href="/">
        Bosh sahifaga qaytish
      </Link>
    </main>
  );
}

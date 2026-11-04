import HeroSection from './components/HeroSection.jsx'
import { CTA, Evidence, Footer, Pipeline, Problem, Stack, Verification } from './components/AquaTraceSections.jsx'

export default function App() {
  return (
    <div className="noise-texture relative min-h-screen bg-abyss">
      <HeroSection />
      <main>
        <Problem />
        <Pipeline />
        <Verification />
        <Evidence />
        <Stack />
        <CTA />
      </main>
      <Footer />
    </div>
  )
}

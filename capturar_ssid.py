"""
Abre um navegador real, você faz login manualmente na Pocket Option,
e o script captura o SSID automaticamente e salva no session.json.
"""
import asyncio
from pocket_brainy.utils.capture_ssid import capture_ssid_async



async def main():
    print("=" * 60)
    print("  Abrindo navegador — faça login na Pocket Option")
    print("  O script captura o SSID automaticamente após o login")
    print("=" * 60)
    print("\n  ▶ Faça login no navegador que abriu...")
    print("  ▶ Aguardando captura do SSID (até 3 minutos)\n")

    ssid = await capture_ssid_async(on_progress=lambda msg: print(f"  {msg}"))

    if not ssid:
        print("\n❌ SSID não capturado.")
        print("   Verifique se o login foi realizado com sucesso e tente novamente.")
        return

    print(f"\n✅ SSID capturado com sucesso!")
    print(f"   {ssid[:60]}...")
    print("\n✅ Sessão salva automaticamente.")
    print("   O bot vai usar esse SSID automaticamente.")
    print("   Quando expirar (~4h), ele renova sozinho pelos cookies.")
    print("\n   💡 Dica: no Telegram envie /ssid <TOKEN> para atualizar")
    print("      o SSID sem precisar reiniciar o bot.")
    print("\n   Pode iniciar o bot agora:  python main.py  🚀")


asyncio.run(main())

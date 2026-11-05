import time
import os, tempfile, shutil
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

from decimal import Decimal
from django.test import TestCase

from rachais.models import Group, Participant, Expense, ExpenseSplit, Payment, Profile
from rachais.views import _calculate_balances, _calculate_settlements

User = get_user_model()


class E2EFullFlowTests(StaticLiveServerTestCase):
    """
    Testa o fluxo completo da aplicação RachAi de forma automatizada
    e com pausas visuais entre as etapas (ideal para screencasts).
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp_profile = tempfile.mkdtemp(prefix="chrome-prof-")

        opts = webdriver.ChromeOptions()

        in_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"

        if in_ci:
            opts.add_argument("--headless=new")
        else:
            pass

        opts.add_argument(f"--user-data-dir={cls._tmp_profile}")

        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-dev-tools")
        opts.add_argument("--disable-extensions")

        if not in_ci:
            opts.add_experimental_option("detach", True)

        try:
            cls.selenium = webdriver.Chrome(options=opts)
            cls.selenium.implicitly_wait(10)
        except Exception:
            shutil.rmtree(cls._tmp_profile, ignore_errors=True)
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.selenium.quit()
        finally:
            if cls.__dict__.get("_tmp_profile"):
                shutil.rmtree(cls._tmp_profile, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        
        self.user1_email = 'criador@teste.com'
        self.user1_pass = 'senhaSuperF0rte'
        self.user1 = User.objects.create_user(
            username='criador',
            first_name='Rafael',
            email=self.user1_email,
            password=self.user1_pass
        )

        self.user2_email = 'amigo@teste.com'
        self.user2_pass = 'outraSenha123'
        self.user2 = User.objects.create_user(
            username='amigo.convidado',
            first_name='Amigo',
            email=self.user2_email,
            password=self.user2_pass
        )

    def _click_tab(self, tab_id):
        """
        Alterna a aba de dívidas clicando no rótulo.

        O <input type="radio"> que controla a aba é escondido por CSS (é o truque
        que faz a aba funcionar sem JS), então só o <label> é clicável.
        """
        self.selenium.find_element(
            By.CSS_SELECTOR, 'label[for="{}"]'.format(tab_id)
        ).click()
        time.sleep(1)

    def _pick_split_method(self, value):
        """Escolhe o método de divisão clicando no segmento (o radio fica escondido)."""
        self.selenium.find_element(
            By.XPATH, "//label[.//input[@id='sm-{}']]".format(value)
        ).click()
        time.sleep(1)

    def _participant_id(self, group_name, user):
        """
        Id do Participant do usuário dentro do grupo.

        Os campos de divisão do formulário são nomeados pelo id do Participant
        (não pelo do User), já que um grupo pode ter participantes sem conta.
        """
        return Participant.objects.get(group__name=group_name, user=user).id

    # ===============================================
    # 1. TESTE DE DIVISÃO IGUAL (OK)
    # ===============================================
    def test_fluxo_completo_do_app(self):
        """
        Simula: Login > Criar Grupo > Convidar Amigo > Adicionar Despesa (IGUAL) > Verificar.
        """
        print("\n\n" + "="*50)
        print("INICIANDO TESTE E2E: FLUXO COMPLETO (DIVISÃO IGUAL)")
        print("="*50)
        
        wait = WebDriverWait(self.selenium, 10)
        delay = 3 
        
        print("\n[FLUXO IGUAL - ETAPA 1/5] - Realizando Login...")
        login_url = self.live_server_url + reverse('accounts:login')
        self.selenium.get(login_url)
        
        email_input = wait.until(EC.presence_of_element_located((By.NAME, 'email')))
        password_input = self.selenium.find_element(By.NAME, 'password')
        submit_button = self.selenium.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        email_input.send_keys(self.user1.email)
        time.sleep(delay)
        password_input.send_keys(self.user1_pass)
        time.sleep(delay)
        submit_button.click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Comece criando um grupo')]")))
        print("-> Login realizado com sucesso.")
        time.sleep(delay)

        print("[FLUXO IGUAL - ETAPA 2/5] - Criando Grupo...")
        self.selenium.find_element(By.LINK_TEXT, 'Criar grupo').click()
        nome_grupo_input = wait.until(EC.presence_of_element_located((By.NAME, 'name')))
        nome_grupo_input.send_keys('Viagem de Férias')
        time.sleep(delay)
        self.selenium.find_element(By.XPATH, "//button[normalize-space()='Criar grupo']").click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Viagem de Férias')]")))
        print("-> Grupo 'Viagem de Férias' criado com sucesso.")
        time.sleep(delay)

        print("[FLUXO IGUAL - ETAPA 3/5] - Adicionando Participante...")
        email_participante_input = self.selenium.find_element(By.NAME, 'identifier')
        email_participante_input.send_keys(self.user2_email)
        time.sleep(delay)
        self.selenium.find_element(By.XPATH, "//form[contains(@action,'add-participant')]//button[@type='submit']").click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'adicionado(a) com sucesso!')]")))
        wait.until(EC.presence_of_element_located((By.XPATH, "//section[contains(@class,'balance-summary')]//span[@class='person-name' and text()='Amigo']")))
        print(f"-> Participante '{self.user2_email}' adicionado com sucesso.")
        time.sleep(delay)

        print("[FLUXO IGUAL - ETAPA 4/5] - Adicionando Despesa (Divisão Igual)...")
        self.selenium.find_element(By.LINK_TEXT, 'Adicionar despesa').click()
        description_input = wait.until(EC.presence_of_element_located((By.NAME, 'description')))
        description_input.send_keys('Jantar na Pizzaria')
        time.sleep(delay)
        self.selenium.find_element(By.NAME, 'amount').send_keys('150,00')
        time.sleep(delay)
        select_element = self.selenium.find_element(By.NAME, 'paid_by')
        select = Select(select_element)
        select.select_by_visible_text('Rafael')
        time.sleep(delay)
        self.selenium.find_element(By.XPATH, "//button[normalize-space()='Salvar despesa']").click()
        
        print("[FLUXO IGUAL - ETAPA 5/5] - Verificando o Resultado...")
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Jantar na Pizzaria')]")))
        page_source = self.selenium.page_source
        self.assertIn('Rafael pagou', page_source)
        self.assertIn('R$ 150,00', page_source)
        print("-> Despesa de R$ 150,00 adicionada e verificada com sucesso.")
        summary_div = wait.until(EC.presence_of_element_located((By.CLASS_NAME, 'balance-summary')))
        summary_text = summary_div.text
        self.assertIn('a receber', summary_text)
        self.assertIn('R$ 75,00', summary_text)
        self.assertIn('a pagar', summary_text)
        print("-> Balanço de saldos (Igual) verificado com sucesso.")
        time.sleep(delay)
        print("\n--> Teste de DIVISÃO IGUAL concluído com sucesso!")


    # ========================================================
    # 2. TESTE DE DIVISÃO POR PORCENTAGEM (COM CORREÇÃO)
    # ========================================================
    def test_fluxo_divisao_porcentagem(self):
        """
        Simula: Login > Criar Grupo > Convidar Amigo > Adicionar Despesa (PORCENTAGEM) > Verificar.
        """
        print("\n\n" + "="*50)
        print("INICIANDO TESTE E2E: DIVISÃO POR PORCENTAGEM")
        print("="*50)
        
        wait = WebDriverWait(self.selenium, 10)
        delay = 3 
        
        print("\n[FLUXO % - ETAPA 1/5] - Realizando Login...")
        login_url = self.live_server_url + reverse('accounts:login')
        self.selenium.get(login_url)
        email_input = wait.until(EC.presence_of_element_located((By.NAME, 'email')))
        email_input.send_keys(self.user1.email)
        self.selenium.find_element(By.NAME, 'password').send_keys(self.user1_pass)
        self.selenium.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Comece criando um grupo')]")))
        print("-> Login realizado com sucesso.")
        time.sleep(delay)

        print("[FLUXO % - ETAPA 2/5] - Criando Grupo...")
        self.selenium.find_element(By.LINK_TEXT, 'Criar grupo').click()
        nome_grupo_input = wait.until(EC.presence_of_element_located((By.NAME, 'name')))
        nome_grupo_input.send_keys('Churrasco 60/40') 
        time.sleep(delay)
        self.selenium.find_element(By.XPATH, "//button[normalize-space()='Criar grupo']").click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Churrasco 60/40')]")))
        print("-> Grupo 'Churrasco 60/40' criado com sucesso.")
        time.sleep(delay)

        print("[FLUXO % - ETAPA 3/5] - Adicionando Participante...")
        self.selenium.find_element(By.NAME, 'identifier').send_keys(self.user2_email)
        time.sleep(delay)
        self.selenium.find_element(By.XPATH, "//form[contains(@action,'add-participant')]//button[@type='submit']").click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'adicionado(a) com sucesso!')]")))
        wait.until(EC.presence_of_element_located((By.XPATH, "//section[contains(@class,'balance-summary')]//span[@class='person-name' and text()='Amigo']")))
        print(f"-> Participante '{self.user2_email}' adicionado com sucesso.")
        time.sleep(delay)

        print("[FLUXO % - ETAPA 4/5] - Adicionando Despesa por PORCENTAGEM...")
        self.selenium.find_element(By.LINK_TEXT, 'Adicionar despesa').click()
        description_input = wait.until(EC.presence_of_element_located((By.NAME, 'description')))
        description_input.send_keys('Carnes do Churrasco')
        time.sleep(delay)
        self.selenium.find_element(By.NAME, 'amount').send_keys('200,00')
        time.sleep(delay)
        select_element = self.selenium.find_element(By.NAME, 'paid_by')
        select_payer = Select(select_element)
        select_payer.select_by_visible_text('Rafael') 
        time.sleep(delay)
        
        self._pick_split_method('UNEQUAL_PERCENTAGE')
        print("-> Método 'Dividir por porcentagem (%)' selecionado.")
        time.sleep(delay)

        
        participant1_id = self._participant_id('Churrasco 60/40', self.user1)
        participant2_id = self._participant_id('Churrasco 60/40', self.user2)

        try:
            perc_input_user1 = wait.until(EC.visibility_of_element_located(
                (By.NAME, f'split_perc_{participant1_id}')
            ))
            perc_input_user2 = self.selenium.find_element(By.NAME, f'split_perc_{participant2_id}')
        except Exception as e:
            print(f"Erro ao encontrar os campos de input: {e}")
            self.fail("Não foi possível localizar os campos de porcentagem.")

 
        self.selenium.execute_script("arguments[0].value = '60.0';", perc_input_user1)
        print(f"-> [JS] Input User 1 ({self.user1.id}): 60.0%")
        time.sleep(1) 

        self.selenium.execute_script("arguments[0].value = '40.0';", perc_input_user2)
        print(f"-> [JS] Input User 2 ({self.user2.id}): 40.0%")
    
        self.selenium.execute_script("arguments[0].dispatchEvent(new Event('change'));", perc_input_user1)
        self.selenium.execute_script("arguments[0].dispatchEvent(new Event('change'));", perc_input_user2)
        
        print("-> Valores injetados e eventos 'change' disparados.")
        time.sleep(delay) 
        

        self.selenium.find_element(By.XPATH, "//button[normalize-space()='Salvar despesa']").click()
        
        print("[FLUXO % - ETAPA 5/5] - Verificando o Resultado...")
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Carnes do Churrasco')]")))
        page_source = self.selenium.page_source
        self.assertIn('Rafael pagou', page_source)
        self.assertIn('R$ 200,00', page_source) 
        print("-> Despesa de R$ 200,00 (Rafael pagou) verificada.")
        time.sleep(delay)
        
        summary_div = wait.until(EC.presence_of_element_located((By.CLASS_NAME, 'balance-summary')))
        summary_text = summary_div.text
        self.assertIn('R$ 80,00', summary_text) 
        self.assertIn('Rafael', summary_text)
        self.assertIn('a receber', summary_text)
        self.assertIn('Amigo', summary_text)
        self.assertIn('a pagar', summary_text)
        print("-> Balanço de saldos (60/40) verificado com sucesso.")
        time.sleep(delay)
        print("\n--> Teste de DIVISÃO PORCENTAGEM concluído com sucesso!")
        
        
    # ========================================================
    # 3. TESTE DE DIVISÃO POR VALOR EXATO (OK)
    # ========================================================
    def test_fluxo_divisao_valor_exato(self):
        """
        Simula: Login > Criar Grupo > Convidar Amigo > Adicionar Despesa (VALOR EXATO) > Verificar.
        """
        print("\n\n" + "="*50)
        print("INICIANDO TESTE E2E: DIVISÃO POR VALOR EXATO")
        print("="*50)
        
        wait = WebDriverWait(self.selenium, 10)
        delay = 3 
        
        print("\n[FLUXO VALOR - ETAPA 1/5] - Realizando Login...")
        login_url = self.live_server_url + reverse('accounts:login')
        self.selenium.get(login_url)
        email_input = wait.until(EC.presence_of_element_located((By.NAME, 'email')))
        email_input.send_keys(self.user1.email)
        self.selenium.find_element(By.NAME, 'password').send_keys(self.user1_pass)
        self.selenium.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Comece criando um grupo')]")))
        print("-> Login realizado com sucesso.")
        time.sleep(delay)

        print("[FLUXO VALOR - ETAPA 2/5] - Criando Grupo...")
        self.selenium.find_element(By.LINK_TEXT, 'Criar grupo').click()
        nome_grupo_input = wait.until(EC.presence_of_element_located((By.NAME, 'name')))
        nome_grupo_input.send_keys('Mercado 100/50') 
        time.sleep(delay)
        self.selenium.find_element(By.XPATH, "//button[normalize-space()='Criar grupo']").click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Mercado 100/50')]")))
        print("-> Grupo 'Mercado 100/50' criado com sucesso.")
        time.sleep(delay)

        print("[FLUXO VALOR - ETAPA 3/5] - Adicionando Participante...")
        self.selenium.find_element(By.NAME, 'identifier').send_keys(self.user2_email)
        time.sleep(delay)
        self.selenium.find_element(By.XPATH, "//form[contains(@action,'add-participant')]//button[@type='submit']").click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'adicionado(a) com sucesso!')]")))
        wait.until(EC.presence_of_element_located((By.XPATH, "//section[contains(@class,'balance-summary')]//span[@class='person-name' and text()='Amigo']")))
        print(f"-> Participante '{self.user2_email}' adicionado com sucesso.")
        time.sleep(delay)

        print("[FLUXO VALOR - ETAPA 4/5] - Adicionando Despesa por VALOR EXATO...")
        self.selenium.find_element(By.LINK_TEXT, 'Adicionar despesa').click()
        description_input = wait.until(EC.presence_of_element_located((By.NAME, 'description')))
        description_input.send_keys('Compras do Mercado')
        time.sleep(delay)
        self.selenium.find_element(By.NAME, 'amount').send_keys('150,00')
        time.sleep(delay)
        select_element = self.selenium.find_element(By.NAME, 'paid_by')
        select_payer = Select(select_element)
        select_payer.select_by_visible_text('Amigo') 
        time.sleep(delay)
        
        self._pick_split_method('UNEQUAL_VALUE')
        print("-> Método 'Dividir por valores exatos (R$)' selecionado.")
        time.sleep(delay) 

        

        participant1_id = self._participant_id('Mercado 100/50', self.user1)
        participant2_id = self._participant_id('Mercado 100/50', self.user2)

        valor_input_user1 = wait.until(EC.visibility_of_element_located(
            (By.NAME, f'split_user_{participant1_id}')
        ))
        valor_input_user1.clear() 
        valor_input_user1.send_keys('100,00')
        print(f"-> Input User 1 ({self.user1.id}): 100,00")
        time.sleep(delay) 

        valor_input_user2 = self.selenium.find_element(By.NAME, f'split_user_{participant2_id}')
        valor_input_user2.clear() 
        valor_input_user2.send_keys('50,00')
        print(f"-> Input User 2 ({self.user2.id}): 50,00")
        time.sleep(delay)
        
        self.selenium.find_element(By.XPATH, "//button[normalize-space()='Salvar despesa']").click()
        
        print("[FLUXO VALOR - ETAPA 5/5] - Verificando o Resultado...")
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Compras do Mercado')]")))
        page_source = self.selenium.page_source
        self.assertIn('Amigo pagou', page_source)
        self.assertIn('R$ 150,00', page_source) 
        print("-> Despesa de R$ 150,00 (Amigo pagou) verificada.")
        time.sleep(delay)
        
        summary_div = wait.until(EC.presence_of_element_located((By.CLASS_NAME, 'balance-summary')))
        summary_text = summary_div.text
        self.assertIn('R$ 100,00', summary_text) 
        self.assertIn('Rafael', summary_text)
        self.assertIn('a pagar', summary_text)
        self.assertIn('Amigo', summary_text)
        self.assertIn('a receber', summary_text)
        print("-> Balanço de saldos (100/50) verificado com sucesso.")
        time.sleep(delay)
        print("\n--> Teste de DIVISÃO VALOR EXATO concluído com sucesso!")


    # ========================================================
    # 4. TESTE: MARCAR DÍVIDA COMO PAGA
    # ========================================================
    def test_marcar_divida_como_paga(self):
        """
        Fluxo: criar grupo, adicionar amigo, criar despesa (divisão igual),
        verificar que o Amigo deve, logar como Amigo, clicar em Pagar
        e checar que a dívida some das pendentes e entra em Quitadas.
        """
        print("\n\n" + "="*50)
        print("INICIANDO TESTE E2E: MARCAR DÍVIDA COMO PAGA")
        print("="*50)

        wait = WebDriverWait(self.selenium, 10)
        delay = 3

        print("[PAGAMENTO - ETAPA 1/10] - Realizando Login (Rafael)...")
        login_url = self.live_server_url + reverse('accounts:login')
        self.selenium.get(login_url)
        email_input = wait.until(EC.presence_of_element_located((By.NAME, 'email')))
        email_input.send_keys(self.user1.email)
        self.selenium.find_element(By.NAME, 'password').send_keys(self.user1_pass)
        self.selenium.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Comece criando um grupo')]")))
        print("-> Login realizado com sucesso.")
        time.sleep(delay)

        print("[PAGAMENTO - ETAPA 2/10] - Criando Grupo...")
        self.selenium.find_element(By.LINK_TEXT, 'Criar grupo').click()
        nome_grupo_input = wait.until(EC.presence_of_element_located((By.NAME, 'name')))
        nome_grupo_input.send_keys('Grupo Pagamento')
        time.sleep(delay)
        self.selenium.find_element(By.XPATH, "//button[normalize-space()='Criar grupo']").click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Grupo Pagamento')]")))
        print("-> Grupo 'Grupo Pagamento' criado com sucesso.")
        time.sleep(delay)

        print("[PAGAMENTO - ETAPA 3/10] - Adicionando Participante...")
        self.selenium.find_element(By.NAME, 'identifier').send_keys(self.user2_email)
        time.sleep(delay)
        self.selenium.find_element(By.XPATH, "//form[contains(@action,'add-participant')]//button[@type='submit']").click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'adicionado(a) com sucesso!')]")))
        wait.until(EC.presence_of_element_located((By.XPATH, "//section[contains(@class,'balance-summary')]//span[@class='person-name' and text()='Amigo']")))
        print(f"-> Participante '{self.user2_email}' adicionado com sucesso.")
        time.sleep(delay)

        print("[PAGAMENTO - ETAPA 4/10] - Adicionando Despesa...")
        self.selenium.find_element(By.LINK_TEXT, 'Adicionar despesa').click()
        description_input = wait.until(EC.presence_of_element_located((By.NAME, 'description')))
        description_input.send_keys('Conta do Bar')
        time.sleep(delay)
        self.selenium.find_element(By.NAME, 'amount').send_keys('100,00')
        time.sleep(delay)
        select_element = self.selenium.find_element(By.NAME, 'paid_by')
        Select(select_element).select_by_visible_text('Rafael')
        time.sleep(delay)

        self.selenium.find_element(By.XPATH, "//button[normalize-space()='Salvar despesa']").click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Conta do Bar')]")))
        print("-> Despesa 'Conta do Bar' (R$ 100,00) adicionada.")
        time.sleep(delay)

        print("[PAGAMENTO - ETAPA 5/10] - Verificando dívida do Amigo no resumo...")
        summary_div = wait.until(EC.presence_of_element_located((By.CLASS_NAME, 'balance-summary')))
        summary_text = summary_div.text
        self.assertIn('Amigo', summary_text)
        self.assertIn('a pagar', summary_text)
        self.assertIn('R$ 50,00', summary_text)
        print("-> Dívida de R$ 50,00 (Amigo) verificada no resumo.")
        time.sleep(delay)

        print("[PAGAMENTO - ETAPA 6/10] - Fazendo logout do Rafael...")
        self.selenium.find_element(By.LINK_TEXT, 'Sair').click()
        time.sleep(delay)

        print("[PAGAMENTO - ETAPA 7/10] - Fazendo login com o Amigo...")
        login_url = self.live_server_url + reverse('accounts:login')
        self.selenium.get(login_url)
        email_input = wait.until(EC.presence_of_element_located((By.NAME, 'email')))
        email_input.send_keys(self.user2_email)
        self.selenium.find_element(By.NAME, 'password').send_keys(self.user2_pass)
        self.selenium.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Seus grupos') or contains(text(), 'Comece criando um grupo')]")))
        print("-> Login do Amigo realizado com sucesso.")
        time.sleep(delay)

        print("[PAGAMENTO - ETAPA 8/10] - Navegando até o grupo 'Grupo Pagamento'...")
        self.selenium.find_element(By.LINK_TEXT, 'Grupo Pagamento').click()
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Conta do Bar')]")))
        time.sleep(delay)

        print("[PAGAMENTO - ETAPA 9/10] - Clicando em 'Pagar' na lista de dívidas pendentes...")
        
        try:
            tab_pendentes = self.selenium.find_element(By.ID, 'tab-pendentes')
            if not tab_pendentes.is_selected():
                self._click_tab('tab-pendentes')
        except:
            pass
        
        pagar_btn = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//div[contains(@class,'debts-panel--pending')]//button[normalize-space()='Pagar']")
            )
        )
        
        pagar_btn.click()
        time.sleep(1)
        
        # A confirmação agora é um modal da página (não mais o alert do navegador).
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, '#confirm-dialog [data-confirm-ok]'))).click()
        print("-> Pagamento confirmado no modal.")
        
        time.sleep(delay)

        print("[PAGAMENTO - ETAPA 10/10] - Aguardando processamento do pagamento...")
        time.sleep(delay)

        print("[PAGAMENTO - ETAPA 11/10] - Verificando que dívida sumiu das pendentes...")
        self._click_tab('tab-pendentes')
        pending_panel = self.selenium.find_element(By.CSS_SELECTOR, '.debts-panel--pending')
        pending_html = pending_panel.get_attribute('innerText')
        
        if 'Nenhuma pendência' in pending_html:
            print("-> Confirmado: 'Nenhuma pendência no momento.'")
        else:
            self.assertNotIn('R$ 50,00', pending_html) or self.assertIn('Nenhuma pendência', pending_html)
            print("-> Dívida de R$ 50,00 não aparece mais nas pendentes.")

        print("[PAGAMENTO - ETAPA 12/10] - Verificando que pagamento aparece em quitadas...")
        self._click_tab('tab-quitadas')
        paid_panel = self.selenium.find_element(By.CSS_SELECTOR, '.debts-panel--paid')
        paid_html = paid_panel.get_attribute('innerText')
        self.assertIn('R$ 50,00', paid_html)
        self.assertIn('Rafael', paid_html)
        print("-> Pagamento de R$ 50,00 para Rafael aparece na aba de quitadas.")

        print("\n--> Teste de MARCAR DÍVIDA COMO PAGA concluído com sucesso!")

# ============================================================
# TESTES DO GRUPO GERENCIADO (participantes sem conta)
# ============================================================
class ManagedGroupTests(TestCase):
    """
    Cobre o modo "Gerenciado por mim": um único usuário cadastra os nomes e os
    gastos de todo mundo, sem que os demais precisem ter conta no RachAí.

    Não usa Selenium — roda rápido e sem navegador.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username='dono', first_name='Dono',
            email='dono@teste.com', password='senhaSuperF0rte'
        )
        self.client.force_login(self.owner)

    def _create_group(self, name, mode):
        self.client.post(reverse('rachais:create_group'), {'name': name, 'mode': mode})
        return Group.objects.get(name=name, creator=self.owner)

    def test_grupo_gerenciado_aceita_participante_sem_conta(self):
        group = self._create_group('Praia', Group.MODE_MANAGED)

        self.client.post(
            reverse('rachais:add_participant', args=[group.id]),
            {'guest_name': 'Marcelo'},
        )

        guest = group.participants.get(guest_name='Marcelo')
        self.assertIsNone(guest.user)
        self.assertTrue(guest.is_guest)
        self.assertEqual(guest.display_name, 'Marcelo')
        # o criador continua sendo participante do próprio grupo
        self.assertEqual(group.participants.count(), 2)

    def test_grupo_gerenciado_recusa_nome_duplicado(self):
        group = self._create_group('Praia', Group.MODE_MANAGED)
        url = reverse('rachais:add_participant', args=[group.id])

        self.client.post(url, {'guest_name': 'Marcelo'})
        self.client.post(url, {'guest_name': 'marcelo'})

        self.assertEqual(group.participants.filter(guest_name__iexact='marcelo').count(), 1)

    def test_grupo_colaborativo_ignora_nome_avulso(self):
        """No modo colaborativo o comportamento antigo é preservado."""
        group = self._create_group('Trabalho', Group.MODE_SHARED)

        self.client.post(
            reverse('rachais:add_participant', args=[group.id]),
            {'guest_name': 'Fulano'},
        )

        self.assertEqual(group.participants.count(), 1)
        self.assertFalse(group.participants.filter(user__isnull=True).exists())

    def test_despesa_dividida_entre_avulsos_e_saldos(self):
        group = self._create_group('Praia', Group.MODE_MANAGED)
        add_participant_url = reverse('rachais:add_participant', args=[group.id])
        self.client.post(add_participant_url, {'guest_name': 'Marcelo'})
        self.client.post(add_participant_url, {'guest_name': 'Pedro'})

        owner_p = group.participants.get(user=self.owner)
        marcelo = group.participants.get(guest_name='Marcelo')
        pedro = group.participants.get(guest_name='Pedro')

        # Marcelo pagou R$ 300 e a conta é dividida igualmente entre os três
        self.client.post(
            reverse('rachais:add_expense', args=[group.id]),
            {
                'description': 'Pousada',
                'amount': '300,00',
                'paid_by': marcelo.id,
                'split_method': 'EQUAL',
            },
        )

        expense = group.expenses.get()
        self.assertEqual(expense.paid_by_id, marcelo.id)
        self.assertEqual(expense.splits.count(), 3)

        balances = _calculate_balances(group)
        self.assertEqual(balances[marcelo.id], Decimal('200.00'))
        self.assertEqual(balances[pedro.id], Decimal('-100.00'))
        self.assertEqual(balances[owner_p.id], Decimal('-100.00'))

        # a tela do grupo renderiza com os nomes dos avulsos
        detail = self.client.get(reverse('rachais:group_detail', args=[group.id]))
        self.assertEqual(detail.status_code, 200)
        page = detail.content.decode()
        self.assertIn('Marcelo pagou', page)
        self.assertIn('Pedro', page)
        self.assertIn('sem conta', page)

        # o formulário de despesa lista os avulsos como possíveis pagadores
        form = self.client.get(reverse('rachais:add_expense', args=[group.id]))
        self.assertEqual(form.status_code, 200)
        self.assertIn(f'value="{pedro.id}"', form.content.decode())

    def test_criador_registra_pagamento_entre_dois_avulsos(self):
        group = self._create_group('Praia', Group.MODE_MANAGED)
        add_participant_url = reverse('rachais:add_participant', args=[group.id])
        self.client.post(add_participant_url, {'guest_name': 'Marcelo'})
        self.client.post(add_participant_url, {'guest_name': 'Pedro'})

        marcelo = group.participants.get(guest_name='Marcelo')
        pedro = group.participants.get(guest_name='Pedro')

        # despesa só entre Marcelo e Pedro: Pedro fica devendo 50 ao Marcelo
        expense = Expense.objects.create(
            group=group, description='Táxi', amount=Decimal('100.00'),
            paid_by=marcelo, split_method='UNEQUAL_VALUE',
        )
        ExpenseSplit.objects.create(expense=expense, participant=marcelo, amount_owed=Decimal('50.00'))
        ExpenseSplit.objects.create(expense=expense, participant=pedro, amount_owed=Decimal('50.00'))

        self.client.post(
            reverse('rachais:pay_debt'),
            {
                'group_id': group.id,
                'payer_id': pedro.id,
                'receiver_id': marcelo.id,
                'amount': '50.00',
            },
        )

        payment = group.payments.get()
        self.assertEqual(payment.payer_id, pedro.id)
        self.assertEqual(payment.receiver_id, marcelo.id)
        self.assertEqual(payment.created_by_id, self.owner.id)
        # dívida quitada: ninguém mais aparece no acerto
        participants = list(group.participants.all())
        self.assertEqual(
            _calculate_settlements(_calculate_balances(group), participants), []
        )

    def test_participante_comum_nao_registra_divida_de_terceiro(self):
        """Só o criador pode quitar dívida em nome de outra pessoa."""
        group = self._create_group('Trabalho', Group.MODE_SHARED)
        outro = User.objects.create_user(
            username='outro', first_name='Outro',
            email='outro@teste.com', password='senhaSuperF0rte'
        )
        self.client.post(
            reverse('rachais:add_participant', args=[group.id]),
            {'identifier': 'outro@teste.com'},
        )

        owner_p = group.participants.get(user=self.owner)
        outro_p = group.participants.get(user=outro)

        expense = Expense.objects.create(
            group=group, description='Café', amount=Decimal('100.00'),
            paid_by=owner_p, split_method='UNEQUAL_VALUE',
        )
        ExpenseSplit.objects.create(expense=expense, participant=outro_p, amount_owed=Decimal('100.00'))

        # 'outro' tenta registrar que o dono pagou alguém — não pode
        self.client.force_login(outro)
        self.client.post(
            reverse('rachais:pay_debt'),
            {
                'group_id': group.id,
                'payer_id': owner_p.id,
                'receiver_id': outro_p.id,
                'amount': '100.00',
            },
        )

        self.assertEqual(group.payments.count(), 0)


# ============================================================
# TESTES DE EDIÇÃO E EXCLUSÃO DE DESPESAS
# ============================================================
class ExpenseEditDeleteTests(TestCase):
    """Corrigir e apagar despesas já lançadas."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='dono', first_name='Dono',
            email='dono@teste.com', password='senhaSuperF0rte'
        )
        self.client.force_login(self.owner)

        self.client.post(
            reverse('rachais:create_group'),
            {'name': 'Praia', 'mode': Group.MODE_MANAGED},
        )
        self.group = Group.objects.get(name='Praia')
        self.client.post(
            reverse('rachais:add_participant', args=[self.group.id]),
            {'guest_name': 'Marcelo'},
        )
        self.owner_p = self.group.participants.get(user=self.owner)
        self.marcelo = self.group.participants.get(guest_name='Marcelo')

        self.client.post(
            reverse('rachais:add_expense', args=[self.group.id]),
            {
                'description': 'Pousada',
                'amount': '100,00',
                'paid_by': self.owner_p.id,
                'split_method': 'EQUAL',
            },
        )
        self.expense = self.group.expenses.get()

    def _edit_url(self):
        return reverse('rachais:edit_expense', args=[self.group.id, self.expense.id])

    def _delete_url(self):
        return reverse('rachais:delete_expense', args=[self.group.id, self.expense.id])

    def test_formulario_de_edicao_vem_preenchido(self):
        response = self.client.get(self._edit_url())
        page = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('Editar despesa', page)
        self.assertIn('value="Pousada"', page)
        self.assertIn('value="100,00"', page)
        # o pagador atual vem marcado como selecionado
        marker = 'value="{}"'.format(self.owner_p.id)
        trecho = page[page.index(marker):page.index(marker) + 120]
        self.assertIn('selected', trecho)

    def test_editar_valor_recalcula_a_divisao(self):
        self.client.post(
            self._edit_url(),
            {
                'description': 'Pousada',
                'amount': '300,00',
                'paid_by': self.owner_p.id,
                'split_method': 'EQUAL',
            },
        )

        self.expense.refresh_from_db()
        self.assertEqual(self.expense.amount, Decimal('300.00'))
        self.assertEqual(self.expense.splits.count(), 2)

        balances = _calculate_balances(self.group)
        self.assertEqual(balances[self.owner_p.id], Decimal('150.00'))
        self.assertEqual(balances[self.marcelo.id], Decimal('-150.00'))

        # editar não duplica a despesa
        self.assertEqual(self.group.expenses.count(), 1)

    def test_editar_trocando_quem_pagou_e_o_metodo(self):
        self.client.post(
            self._edit_url(),
            {
                'description': 'Pousada e café',
                'amount': '100,00',
                'paid_by': self.marcelo.id,
                'split_method': 'UNEQUAL_VALUE',
                'split_user_{}'.format(self.owner_p.id): '70,00',
                'split_user_{}'.format(self.marcelo.id): '30,00',
            },
        )

        self.expense.refresh_from_db()
        self.assertEqual(self.expense.description, 'Pousada e café')
        self.assertEqual(self.expense.paid_by_id, self.marcelo.id)
        self.assertEqual(self.expense.split_method, 'UNEQUAL_VALUE')

        owed = {s.participant_id: s.amount_owed for s in self.expense.splits.all()}
        self.assertEqual(owed[self.owner_p.id], Decimal('70.00'))
        self.assertEqual(owed[self.marcelo.id], Decimal('30.00'))

    def test_edicao_invalida_nao_altera_a_despesa(self):
        response = self.client.post(
            self._edit_url(),
            {
                'description': 'Pousada',
                'amount': '100,00',
                'paid_by': self.owner_p.id,
                'split_method': 'UNEQUAL_VALUE',
                'split_user_{}'.format(self.owner_p.id): '10,00',
                'split_user_{}'.format(self.marcelo.id): '10,00',
            },
        )

        self.assertEqual(response.status_code, 200)  # reexibe o formulário
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.amount, Decimal('100.00'))
        self.assertEqual(self.expense.split_method, 'EQUAL')
        self.assertEqual(self.expense.splits.count(), 2)

    def test_apagar_despesa_remove_divisoes_e_zera_saldos(self):
        self.client.post(self._delete_url())

        self.assertEqual(self.group.expenses.count(), 0)
        self.assertEqual(ExpenseSplit.objects.count(), 0)
        self.assertEqual(_calculate_balances(self.group), {})

    def test_apagar_exige_post(self):
        response = self.client.get(self._delete_url())

        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.group.expenses.count(), 1)

    def test_estranho_nao_edita_nem_apaga(self):
        estranho = User.objects.create_user(
            username='estranho', email='estranho@teste.com', password='senhaSuperF0rte'
        )
        self.client.force_login(estranho)

        self.assertEqual(self.client.get(self._edit_url()).status_code, 404)
        self.assertEqual(self.client.post(self._delete_url()).status_code, 404)
        self.assertEqual(self.group.expenses.count(), 1)

    def test_participante_edita_apenas_a_propria_despesa(self):
        """Num grupo colaborativo, cada um corrige o que pagou."""
        self.client.post(
            reverse('rachais:create_group'),
            {'name': 'Trabalho', 'mode': Group.MODE_SHARED},
        )
        group = Group.objects.get(name='Trabalho')
        outro = User.objects.create_user(
            username='outro', first_name='Outro',
            email='outro@teste.com', password='senhaSuperF0rte'
        )
        self.client.post(
            reverse('rachais:add_participant', args=[group.id]),
            {'identifier': 'outro@teste.com'},
        )
        owner_p = group.participants.get(user=self.owner)
        outro_p = group.participants.get(user=outro)

        minha = Expense.objects.create(
            group=group, description='Café', amount=Decimal('10.00'),
            paid_by=owner_p, split_method='EQUAL',
        )
        dele = Expense.objects.create(
            group=group, description='Almoço', amount=Decimal('20.00'),
            paid_by=outro_p, split_method='EQUAL',
        )

        self.client.force_login(outro)

        # não é criador nem pagador: não mexe
        response = self.client.post(
            reverse('rachais:delete_expense', args=[group.id, minha.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Expense.objects.filter(pk=minha.id).exists())

        # a própria despesa, pode
        self.client.post(reverse('rachais:delete_expense', args=[group.id, dele.id]))
        self.assertFalse(Expense.objects.filter(pk=dele.id).exists())

    def test_criador_apaga_despesa_de_participante_avulso(self):
        despesa_avulso = Expense.objects.create(
            group=self.group, description='Táxi', amount=Decimal('40.00'),
            paid_by=self.marcelo, split_method='EQUAL',
        )
        ExpenseSplit.objects.create(
            expense=despesa_avulso, participant=self.marcelo, amount_owed=Decimal('40.00')
        )

        self.client.post(
            reverse('rachais:delete_expense', args=[self.group.id, despesa_avulso.id])
        )

        self.assertFalse(Expense.objects.filter(pk=despesa_avulso.id).exists())


# ============================================================
# TESTES DE PAGAMENTOS ÓRFÃOS
# ============================================================
class OrphanPaymentTests(TestCase):
    """
    Um pagamento só existe para quitar uma dívida vinda das despesas. Se a
    despesa some ou encolhe, o acerto sem lastro tem que ser desfeito — senão
    ele sozinho geraria dívida no sentido contrário.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username='dono', first_name='Dono',
            email='dono@teste.com', password='senhaSuperF0rte'
        )
        self.client.force_login(self.owner)

        self.client.post(
            reverse('rachais:create_group'),
            {'name': 'Praia', 'mode': Group.MODE_MANAGED},
        )
        self.group = Group.objects.get(name='Praia')
        self.client.post(
            reverse('rachais:add_participant', args=[self.group.id]),
            {'guest_name': 'Marcelo'},
        )
        self.owner_p = self.group.participants.get(user=self.owner)
        self.marcelo = self.group.participants.get(guest_name='Marcelo')

    def _add_expense(self, amount, paid_by):
        """Despesa dividida meio a meio entre o dono e o Marcelo."""
        expense = Expense.objects.create(
            group=self.group, description='Pousada',
            amount=Decimal(amount), paid_by=paid_by, split_method='UNEQUAL_VALUE',
        )
        metade = (Decimal(amount) / 2).quantize(Decimal('0.01'))
        ExpenseSplit.objects.create(expense=expense, participant=self.owner_p, amount_owed=metade)
        ExpenseSplit.objects.create(expense=expense, participant=self.marcelo, amount_owed=metade)
        return expense

    def test_apagar_ultima_despesa_desfaz_os_pagamentos(self):
        """O caso do bug: sem despesa nenhuma, tudo tem que zerar."""
        expense = self._add_expense('100.00', self.marcelo)

        # o dono deve 50 ao Marcelo e quita
        self.client.post(
            reverse('rachais:pay_debt'),
            {
                'group_id': self.group.id,
                'payer_id': self.owner_p.id,
                'receiver_id': self.marcelo.id,
                'amount': '50.00',
            },
        )
        self.assertEqual(self.group.payments.count(), 1)

        self.client.post(
            reverse('rachais:delete_expense', args=[self.group.id, expense.id])
        )

        self.assertEqual(self.group.payments.count(), 0)
        self.assertEqual(_calculate_balances(self.group), {})
        participants = list(self.group.participants.all())
        self.assertEqual(
            _calculate_settlements(_calculate_balances(self.group), participants), []
        )

    def test_pagamento_com_lastro_e_preservado(self):
        """Apagar UMA despesa não pode derrubar acerto que as outras sustentam."""
        manter = self._add_expense('100.00', self.marcelo)
        extra = self._add_expense('100.00', self.marcelo)

        # dono deve 100 no total; quita 50 (lastro de sobra)
        self.client.post(
            reverse('rachais:pay_debt'),
            {
                'group_id': self.group.id,
                'payer_id': self.owner_p.id,
                'receiver_id': self.marcelo.id,
                'amount': '100.00',
            },
        )
        self.assertEqual(self.group.payments.count(), 1)

        self.client.post(
            reverse('rachais:delete_expense', args=[self.group.id, extra.id])
        )

        # sobra uma despesa de 100: o dono ainda devia 50, mas pagou 100.
        # o excesso não tem lastro, então o acerto cai.
        self.assertEqual(self.group.payments.count(), 0)

        # já com um acerto do tamanho certo, ele se mantém
        self.client.post(
            reverse('rachais:pay_debt'),
            {
                'group_id': self.group.id,
                'payer_id': self.owner_p.id,
                'receiver_id': self.marcelo.id,
                'amount': '50.00',
            },
        )
        self.assertEqual(self.group.payments.count(), 1)
        self.assertTrue(Expense.objects.filter(pk=manter.id).exists())

    def test_reduzir_valor_na_edicao_desfaz_excesso(self):
        expense = self._add_expense('100.00', self.marcelo)
        self.client.post(
            reverse('rachais:pay_debt'),
            {
                'group_id': self.group.id,
                'payer_id': self.owner_p.id,
                'receiver_id': self.marcelo.id,
                'amount': '50.00',
            },
        )

        # a despesa era menor do que se pensava: 20, não 100
        self.client.post(
            reverse('rachais:edit_expense', args=[self.group.id, expense.id]),
            {
                'description': 'Pousada',
                'amount': '20,00',
                'paid_by': self.marcelo.id,
                'split_method': 'EQUAL',
            },
        )

        # o dono agora só devia 10, mas tinha pago 50 — o acerto perdeu o lastro
        self.assertEqual(self.group.payments.count(), 0)
        balances = _calculate_balances(self.group)
        self.assertEqual(balances[self.marcelo.id], Decimal('10.00'))
        self.assertEqual(balances[self.owner_p.id], Decimal('-10.00'))

    def test_editar_sem_mexer_no_valor_preserva_pagamento(self):
        expense = self._add_expense('100.00', self.marcelo)
        self.client.post(
            reverse('rachais:pay_debt'),
            {
                'group_id': self.group.id,
                'payer_id': self.owner_p.id,
                'receiver_id': self.marcelo.id,
                'amount': '50.00',
            },
        )

        self.client.post(
            reverse('rachais:edit_expense', args=[self.group.id, expense.id]),
            {
                'description': 'Pousada da praia',
                'amount': '100,00',
                'paid_by': self.marcelo.id,
                'split_method': 'EQUAL',
            },
        )

        expense.refresh_from_db()
        self.assertEqual(expense.description, 'Pousada da praia')
        self.assertEqual(self.group.payments.count(), 1)
        self.assertEqual(_calculate_balances(self.group)[self.owner_p.id], Decimal('0.00'))

    def test_avisa_quando_desfaz_pagamento(self):
        expense = self._add_expense('100.00', self.marcelo)
        self.client.post(
            reverse('rachais:pay_debt'),
            {
                'group_id': self.group.id,
                'payer_id': self.owner_p.id,
                'receiver_id': self.marcelo.id,
                'amount': '50.00',
            },
        )

        response = self.client.post(
            reverse('rachais:delete_expense', args=[self.group.id, expense.id]),
            follow=True,
        )

        avisos = [str(m) for m in response.context['messages']]
        self.assertTrue(
            any('sem dívida correspondente' in a for a in avisos),
            f'nenhum aviso sobre pagamento desfeito em {avisos}',
        )


# ============================================================
# TESTES DE DESFAZER PAGAMENTO
# ============================================================
class UndoPaymentTests(TestCase):
    """Desfazer um acerto registrado por engano, pela aba Quitadas."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='dono', first_name='Dono',
            email='dono@teste.com', password='senhaSuperF0rte'
        )
        self.outro = User.objects.create_user(
            username='outro', first_name='Outro',
            email='outro@teste.com', password='senhaSuperF0rte'
        )
        self.client.force_login(self.owner)

        self.client.post(
            reverse('rachais:create_group'),
            {'name': 'Trabalho', 'mode': Group.MODE_SHARED},
        )
        self.group = Group.objects.get(name='Trabalho')
        self.client.post(
            reverse('rachais:add_participant', args=[self.group.id]),
            {'identifier': 'outro@teste.com'},
        )
        self.owner_p = self.group.participants.get(user=self.owner)
        self.outro_p = self.group.participants.get(user=self.outro)

        # o dono pagou 100; o 'outro' deve 50 e quita
        expense = Expense.objects.create(
            group=self.group, description='Café', amount=Decimal('100.00'),
            paid_by=self.owner_p, split_method='UNEQUAL_VALUE',
        )
        ExpenseSplit.objects.create(expense=expense, participant=self.owner_p, amount_owed=Decimal('50.00'))
        ExpenseSplit.objects.create(expense=expense, participant=self.outro_p, amount_owed=Decimal('50.00'))

        self.client.force_login(self.outro)
        self.client.post(
            reverse('rachais:pay_debt'),
            {
                'group_id': self.group.id,
                'receiver_id': self.owner_p.id,
                'amount': '50.00',
            },
        )
        self.payment = self.group.payments.get()

    def _undo_url(self):
        return reverse('rachais:undo_payment', args=[self.group.id, self.payment.id])

    def test_quem_pagou_desfaz_e_a_divida_volta(self):
        self.assertEqual(_calculate_balances(self.group)[self.outro_p.id], Decimal('0.00'))

        self.client.force_login(self.outro)
        self.client.post(self._undo_url())

        self.assertEqual(self.group.payments.count(), 0)
        self.assertEqual(_calculate_balances(self.group)[self.outro_p.id], Decimal('-50.00'))
        self.assertEqual(_calculate_balances(self.group)[self.owner_p.id], Decimal('50.00'))

    def test_criador_do_grupo_desfaz_pagamento_alheio(self):
        self.client.force_login(self.owner)
        self.client.post(self._undo_url())

        self.assertEqual(self.group.payments.count(), 0)

    def test_terceiro_nao_desfaz(self):
        estranho = User.objects.create_user(
            username='estranho', email='estranho@teste.com', password='senhaSuperF0rte'
        )
        self.client.force_login(estranho)

        response = self.client.post(self._undo_url())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.group.payments.count(), 1)

    def test_quem_recebeu_nao_desfaz_sozinho(self):
        """Quem recebeu não desfaz — a não ser que também seja o criador."""
        recebedor = User.objects.create_user(
            username='recebedor', first_name='Recebedor',
            email='recebedor@teste.com', password='senhaSuperF0rte'
        )
        self.client.force_login(self.owner)
        self.client.post(
            reverse('rachais:add_participant', args=[self.group.id]),
            {'identifier': 'recebedor@teste.com'},
        )
        recebedor_p = self.group.participants.get(user=recebedor)
        pagamento = Payment.objects.create(
            group=self.group, payer=self.outro_p, receiver=recebedor_p,
            amount=Decimal('10.00'), created_by=self.outro,
        )

        self.client.force_login(recebedor)
        response = self.client.post(
            reverse('rachais:undo_payment', args=[self.group.id, pagamento.id])
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Payment.objects.filter(pk=pagamento.id).exists())

    def test_desfazer_exige_post(self):
        self.client.force_login(self.outro)

        response = self.client.get(self._undo_url())

        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.group.payments.count(), 1)

    def test_botao_aparece_na_aba_quitadas(self):
        self.client.force_login(self.outro)

        page = self.client.get(
            reverse('rachais:group_detail', args=[self.group.id])
        ).content.decode()

        self.assertIn('Desfazer', page)
        self.assertIn(self._undo_url(), page)

    def test_botao_nao_aparece_para_quem_nao_pode(self):
        """O recebedor vê o pagamento na aba Quitadas, mas sem o botão."""
        self.client.force_login(self.owner)
        self.group.creator = self.outro  # tira o dono da posição de gerente
        self.group.save()

        page = self.client.get(
            reverse('rachais:group_detail', args=[self.group.id])
        ).content.decode()

        self.assertIn('R$ 50,00', page)
        self.assertNotIn(self._undo_url(), page)


class InviteLinkTests(TestCase):
    """Convite por link em grupos colaborativos, inclusive para quem ainda não tem conta."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="dono@ex.com", email="dono@ex.com", password="senha12345", first_name="Dono"
        )
        self.group = Group.objects.create(name="Viagem", creator=self.owner, mode=Group.MODE_SHARED)
        Participant.objects.create(group=self.group, user=self.owner)
        self.token = self.group.ensure_invite_token()
        self.join_url = reverse("rachais:join_group", args=[self.token])

    def test_owner_sees_invite_link_on_group_page(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("rachais:group_detail", args=[self.group.id]))
        self.assertContains(resp, self.join_url)

    def test_managed_group_has_no_invite_link(self):
        managed = Group.objects.create(name="Bar", creator=self.owner, mode=Group.MODE_MANAGED)
        Participant.objects.create(group=managed, user=self.owner)
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("rachais:group_detail", args=[managed.id]))
        self.assertIsNone(resp.context["invite_url"])
        managed.refresh_from_db()
        self.assertIsNone(managed.invite_token)
        token = managed.ensure_invite_token()
        self.client.force_login(User.objects.create_user(username="x@ex.com", password="senha12345"))
        resp = self.client.get(reverse("rachais:join_group", args=[token]))
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_sees_both_options(self):
        resp = self.client.get(self.join_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="guest_name"')
        self.assertContains(resp, f"{reverse('accounts:signup')}?next={self.join_url}")
        self.assertContains(resp, f"{reverse('accounts:login')}?next={self.join_url}")

    def test_new_user_signs_up_from_invite_and_joins(self):
        resp = self.client.post(
            f"{reverse('accounts:signup')}?next={self.join_url}",
            {
                "first_name": "Nova",
                "email": "nova@ex.com",
                "password": "senha12345",
                "confirm_password": "senha12345",
                "next": self.join_url,
            },
        )
        self.assertRedirects(resp, self.join_url)

        # GET só mostra o convite; quem entra é o POST.
        resp = self.client.get(self.join_url)
        self.assertContains(resp, "Viagem")
        self.assertFalse(self.group.participants.filter(user__username="nova@ex.com").exists())

        resp = self.client.post(self.join_url)
        self.assertRedirects(resp, reverse("rachais:group_detail", args=[self.group.id]))
        self.assertTrue(self.group.participants.filter(user__username="nova@ex.com").exists())

    def test_existing_user_logs_in_from_invite(self):
        User.objects.create_user(username="amigo@ex.com", email="amigo@ex.com", password="senha12345")
        resp = self.client.post(
            f"{reverse('accounts:login')}?next={self.join_url}",
            {"email": "amigo@ex.com", "password": "senha12345", "next": self.join_url},
        )
        self.assertRedirects(resp, self.join_url)

    def test_member_opening_link_goes_to_group(self):
        self.client.force_login(self.owner)
        resp = self.client.get(self.join_url)
        self.assertRedirects(resp, reverse("rachais:group_detail", args=[self.group.id]))
        self.assertEqual(self.group.participants.filter(user=self.owner).count(), 1)

    def test_regenerate_invalidates_old_link(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("rachais:regenerate_invite", args=[self.group.id]))
        self.group.refresh_from_db()
        self.assertNotEqual(self.group.invite_token, self.token)

        self.client.force_login(User.objects.create_user(username="y@ex.com", password="senha12345"))
        resp = self.client.get(self.join_url)
        self.assertEqual(resp.status_code, 404)

    def test_only_creator_can_regenerate(self):
        other = User.objects.create_user(username="z@ex.com", password="senha12345")
        Participant.objects.create(group=self.group, user=other)
        self.client.force_login(other)
        self.client.post(reverse("rachais:regenerate_invite", args=[self.group.id]))
        self.group.refresh_from_db()
        self.assertEqual(self.group.invite_token, self.token)

    def test_login_ignores_external_next(self):
        User.objects.create_user(username="w@ex.com", email="w@ex.com", password="senha12345")
        resp = self.client.post(
            f"{reverse('accounts:login')}?next=https://evil.example.com/",
            {"email": "w@ex.com", "password": "senha12345"},
        )
        self.assertRedirects(resp, reverse("rachais:home"), fetch_redirect_response=False)


class GuestJoinTests(TestCase):
    """Entrar num grupo colaborativo só com um nome, e completar o cadastro depois."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="dono@ex.com", email="dono@ex.com", password="senha12345", first_name="Dono"
        )
        self.group = Group.objects.create(name="Casa de praia", creator=self.owner, mode=Group.MODE_SHARED)
        self.owner_part = Participant.objects.create(group=self.group, user=self.owner)
        self.join_url = reverse("rachais:join_group", args=[self.group.ensure_invite_token()])

    def _join_as_guest(self, name="Carla"):
        return self.client.post(self.join_url, {"guest_name": name})

    def test_guest_joins_with_name_and_uses_group(self):
        resp = self._join_as_guest("  Carla   Souza ")
        self.assertRedirects(resp, reverse("rachais:group_detail", args=[self.group.id]))

        part = self.group.participants.get(user__first_name="Carla Souza")
        self.assertTrue(part.user.profile.is_guest)
        self.assertFalse(part.user.has_usable_password())
        self.assertTrue(self.client.session.get("is_guest"))

        # Participa como qualquer membro: lança despesa dividida com o grupo.
        resp = self.client.post(
            reverse("rachais:add_expense", args=[self.group.id]),
            {"description": "Mercado", "amount": "100,00", "paid_by": part.id, "split_method": "EQUAL"},
        )
        self.assertRedirects(resp, reverse("rachais:group_detail", args=[self.group.id]))
        resp = self.client.get(reverse("rachais:group_detail", args=[self.group.id]))
        self.assertContains(resp, "Complete seu cadastro")

    def test_guest_name_is_required_and_unique_in_group(self):
        resp = self._join_as_guest("   ")
        self.assertEqual(resp.status_code, 200)
        resp = self._join_as_guest("dono")
        self.assertContains(resp, "Já tem alguém com esse nome")
        self.assertEqual(self.group.participants.count(), 1)

    def test_guest_access_link_logs_back_in_only_on_post(self):
        self._join_as_guest()
        token = Profile.objects.get(user__first_name="Carla").access_token
        access_url = reverse("accounts:guest_access", args=[token])
        self.client.logout()

        resp = self.client.get(access_url)
        self.assertContains(resp, "Entrar como Carla?")
        self.assertNotIn("_auth_user_id", self.client.session)

        self.client.post(access_url)
        resp = self.client.get(reverse("rachais:group_detail", args=[self.group.id]))
        self.assertEqual(resp.status_code, 200)

    def test_guest_completes_signup_and_keeps_groups(self):
        self._join_as_guest()
        guest = User.objects.get(first_name="Carla")
        token = guest.profile.access_token

        resp = self.client.post(reverse("accounts:account"), {
            "action": "finish",
            "email": "carla@ex.com",
            "password": "senha12345",
            "confirm_password": "senha12345",
        })
        self.assertRedirects(resp, reverse("accounts:account"))

        guest.refresh_from_db()
        self.assertEqual(guest.email, "carla@ex.com")
        self.assertFalse(guest.profile.is_guest)
        self.assertIsNone(guest.profile.access_token)
        self.assertNotIn("is_guest", self.client.session)
        # Continua logada (a troca de senha não derruba a sessão).
        self.assertEqual(self.client.get(reverse("rachais:group_detail", args=[self.group.id])).status_code, 200)

        # O link de convidado morre, e o login normal passa a valer.
        self.client.logout()
        resp = self.client.get(reverse("accounts:guest_access", args=[token]))
        self.assertRedirects(resp, reverse("accounts:login"))
        resp = self.client.post(reverse("accounts:login"), {"email": "carla@ex.com", "password": "senha12345"})
        self.assertRedirects(resp, reverse("rachais:home"), fetch_redirect_response=False)
        self.assertTrue(self.group.participants.filter(user=guest).exists())

    def test_guest_cannot_finish_with_taken_email(self):
        self._join_as_guest()
        self.client.post(reverse("accounts:account"), {
            "action": "finish",
            "email": "dono@ex.com",
            "password": "senha12345",
            "confirm_password": "senha12345",
        })
        self.assertTrue(User.objects.get(first_name="Carla").profile.is_guest)

    def test_regular_user_cannot_use_finish(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("accounts:account"), {
            "action": "finish",
            "email": "outro@ex.com",
            "password": "outrasenha1",
            "confirm_password": "outrasenha1",
        })
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.email, "dono@ex.com")


class PixKeyTests(TestCase):
    """Chave Pix no cadastro, em Minha conta e no card de cada pessoa do grupo."""

    def test_signup_saves_pix_and_group_shows_it(self):
        self.client.post(reverse("accounts:signup"), {
            "first_name": "Ana",
            "email": "ana@ex.com",
            "password": "senha12345",
            "confirm_password": "senha12345",
            "pix_key": "  ana@pix.com ",
        })
        ana = User.objects.get(username="ana@ex.com")
        self.assertEqual(ana.profile.pix_key, "ana@pix.com")

        group = Group.objects.create(name="Bar", creator=ana, mode=Group.MODE_SHARED)
        Participant.objects.create(group=group, user=ana)
        resp = self.client.get(reverse("rachais:group_detail", args=[group.id]))
        self.assertContains(resp, 'data-person-pix="ana@pix.com"')

    def test_signup_without_pix_still_works(self):
        resp = self.client.post(reverse("accounts:signup"), {
            "first_name": "Bia",
            "email": "bia@ex.com",
            "password": "senha12345",
            "confirm_password": "senha12345",
        })
        self.assertRedirects(resp, reverse("rachais:home"), fetch_redirect_response=False)
        self.assertEqual(User.objects.get(username="bia@ex.com").profile.pix_key, "")

    def test_account_updates_pix_for_user_without_profile(self):
        # Contas criadas antes desta mudança não têm Profile.
        user = User.objects.create_user(username="old@ex.com", email="old@ex.com", password="senha12345", first_name="Old")
        self.client.force_login(user)
        self.client.post(reverse("accounts:account"), {
            "action": "profile", "first_name": "Velho", "pix_key": "81999990000",
        })
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Velho")
        self.assertEqual(user.profile.pix_key, "81999990000")

    def test_pix_too_long_is_rejected(self):
        user = User.objects.create_user(username="x@ex.com", password="senha12345", first_name="X")
        self.client.force_login(user)
        resp = self.client.post(reverse("accounts:account"), {
            "action": "profile", "first_name": "X", "pix_key": "a" * 78,
        }, follow=True)
        self.assertContains(resp, "no máximo 77")
        self.assertEqual(Profile.for_user(user).pix_key, "")

    def test_participant_without_profile_has_empty_pix(self):
        user = User.objects.create_user(username="y@ex.com", password="senha12345")
        group = Group.objects.create(name="G", creator=user)
        self.assertEqual(Participant.objects.create(group=group, user=user).pix_key, "")
        self.assertEqual(Participant.objects.create(group=group, guest_name="Avulso").pix_key, "")

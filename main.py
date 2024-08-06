import time
import logging
import numpy
import threading
import math
import pymssql
import RPi.GPIO as GPIO
from flask import Flask, jsonify
from datetime import datetime, timedelta

app = Flask(__name__)

# Configuração básica do logging
logging.basicConfig(
    filename="app.log",
    level=logging.INFO,
    format="%(asctime)s;%(levelname)s;%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Setup GPIO
COUNTER_PIN = 22
DOOR_PIN = 23

GPIO.setmode(GPIO.BCM)
GPIO.setup(DOOR_PIN, GPIO.OUT)
GPIO.setup(COUNTER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

DB_Server = "" # REDACTED FOR PRIVACY
DB_User = "" # REDACTED FOR PRIVACY
DB_Password = "" # REDACTED FOR PRIVACY
DB_DB = "" # REDACTED FOR PRIVACY

# 0: Parado
# 1: Contagem
# 2: Pausa
EstadoContador = 0
Flop = False

ContadorConfigurado = 0  # Registar se o contador está configurado ou não
Quebras = 0

# Estatisticas
TempoInicio = ""
TempoFim = ""
EstatisticaGFANominal = 0
EstatisticaGFA = []
EstatisticaGFAMedia = []
EstatisticaTempo = []
EstatisticaCadenciaArtigo = []
RegistoParagem = 0
GravarDados = 0
Paragens = []

ArtigoEmContagem = "NA"
DescricaoArtigoEmContagem = "NA"
CadenciaArtigoEmContagem = 6000

ContagemAtual = 0
ContagemTotal = 0
# 0: Parado
# 1: Contagem
# 2: Pausa
EstadoContador = 0
# 0: Fechado
# 1: Aberto
EstadoPorta = 0
Ordem = "NA"
input_state = 0  # Estado da leitura do GPIO. Server para o FLIP FLOP
IdBDOrdemProducao = 0  # Id da ordem de produção na BD
ContadorConfigurado = 0  # Registar se o contador está configurado ou não


def open_door():
    global EstadoPorta

    EstadoPorta = 1
    GPIO.output(DOOR_PIN, GPIO.HIGH)
    logging.info("Porta aberta")


def close_door():
    global EstadoPorta

    EstadoPorta = 0
    GPIO.output(DOOR_PIN, GPIO.LOW)
    logging.info("Porta fechada")


def reset_stats():
    global TempoInicio
    global TempoFim
    global EstatisticaGFANominal
    global EstatisticaGFA
    global EstatisticaGFAMedia
    global EstatisticaTempo
    global EstatisticaCadenciaArtigo
    global RegistoParagem

    # Reset aos dados estatisticos
    TempoInicio = ""
    TempoFim = ""
    EstatisticaGFANominal = 0
    EstatisticaGFA = []
    EstatisticaGFAMedia = []
    EstatisticaTempo = []
    EstatisticaCadenciaArtigo = []
    RegistoParagem = 0


def reset_counter():
    global ArtigoEmContagem
    global DescricaoArtigoEmContagem
    global CadenciaArtigoEmContagem
    global ContagemAtual
    global ContagemTotal
    global Quebras
    global EstadoContador
    global EstadoPorta
    global Ordem
    global input_state
    global IdBDOrdemProducao
    global ContadorConfigurado

    ArtigoEmContagem = "NA"
    DescricaoArtigoEmContagem = "NA"
    CadenciaArtigoEmContagem = 6000
    ContagemAtual = 0
    ContagemTotal = 0
    Quebras = 0
    EstadoContador = 0
    EstadoPorta = 0
    Ordem = "NA"
    input_state = 0
    IdBDOrdemProducao = 0
    ContadorConfigurado = 0
    reset_stats()


@app.route("/abrir-porta", methods=["GET"])
def abrir_porta():
    open_door()
    return jsonify({"status": "OK"}), 200


@app.route("/fechar-porta", methods=["GET"])
def fechar_porta():
    close_door()
    return jsonify({"status": "OK"}), 200


@app.route("/iniciar-contagem", methods=["GET"])
def iniciar_contagem():
    global EstadoContador
    global TempoInicio

    reset_stats()
    EstadoContador = 1
    time.sleep(0.5)  # Aguardar 1/2 segundo para as threads acompanharem
    TempoInicio = str(datetime.now().replace(microsecond=0))
    open_door()
    logging.info("Contagem iniciada")

    return jsonify({"status": "OK"}), 200


@app.route("/parar-contagem", methods=["GET"])
def parar_contagem():
    global EstadoContador
    global ContadorConfigurado
    global TempoFim
    global GravarDados

    EstadoContador = 0
    ContadorConfigurado = 0
    TempoFim = str(datetime.now().replace(microsecond=0))
    GravarDados = 1

    close_door()
    time.sleep(5)  # Delay para de 5 segundos para registar na BD
    logging.info("Contagem parada")

    # return jsonify({"status": "OK"}), 200


@app.route("/pausa", methods=["GET"])
def pausa():
    global EstadoContador

    if EstadoContador == 1:
        close_door()  # Fechar a porta
        EstadoContador = 2  # Marcar como pausado
        logging.info("Pausa na contagem")
    # else:
    # return jsonify({"status": "Contador não está ativo"}), 400

    # return jsonify({"status": "OK"}), 200


@app.route("/retomar", methods=["GET"])
def retomar():
    global EstadoContador

    if EstadoContador == 2:
        abrir_porta()  # Abrir a porta
        EstadoContador = 1  # Marcar como ativo
        logging.info("Contagem retomada")
    # else:
    # return jsonify({"status": "Contador não está em pausa"}), 400

    # return jsonify({"status": "OK"}), 200


@app.route("/quebra/<int:valor>", methods=["GET"])
def quebra(valor):
    global Quebras
    global EstadoContador

    if EstadoContador == 1:
        Quebras += valor
        logging.info(f"Registada quebra de {valor} garrafas")
    # else:
    # return jsonify({"status": "Contador não está ativo"}), 400

    # return jsonify({"status": "OK"}), 200


# Confirmar se na base de dados está OK para gravar. BETA
def validate_active_orders():
    global DB_Server
    global DB_User
    global DB_Password
    global DB_DB

    # Devolve um quando na BD tem mais do que uma ordem em ativo
    conn = pymssql.connect(DB_Server, DB_User, DB_Password, DB_DB)
    cursor = conn.cursor()

    # SQL -> Verificar se na BD está ok para gravar nova ordem para contagem
    SQL = """
        IF EXISTS (SELECT Id FROM krones_contadoreslinha WHERE Ativo = 1)
            SELECT COUNT(Id) AS Id FROM krones_contadoreslinha WHERE Ativo = 1
        ELSE
            SELECT '-1' AS Id
    """

    cursor.execute(SQL)
    row = cursor.fetchone()
    if row[0] == "-1":
        return 0
    else:
        return 1


def media_producao():
    global EstatisticaGFA

    if not EstatisticaGFA:
        return 0

    return round(numpy.mean(EstatisticaGFA), 0)


@app.route("/setup/<string:ordem>/<int:cnt>", methods=["GET"])
def setup_contagem(ordem, cnt):
    global DB_Server
    global DB_User
    global DB_Password
    global DB_DB

    global ContadorConfigurado
    global EstadoContador
    global EstadoContador
    global ContagemTotal
    global Ordem
    global ContagemAtual
    global Quebras
    global ArtigoEmContagem
    global DescricaoArtigoEmContagem
    global CadenciaArtigoEmContagem
    global IdBDOrdemProducao

    if ContadorConfigurado == 1:
        logging.info("Contador já configurado")
        # return jsonify({"mean": "Contador já configurado"}), 200

    if validate_active_orders() == 1:
        logging.info("O contador está a registar, por favor aguarde.")
        # return jsonify({"mean": "O contador está a registar, por favor aguarde."}), 200

    if EstadoContador == 0 and ContadorConfigurado == 0:
        try:
            # Reset às estatisticas. Limpar dados anteriores
            reset_stats()
            ContadorConfigurado = 1
            ContagemTotal = cnt
            Ordem = ordem
            ContagemAtual = 0
            Quebras = 0

            # Registar contagem na base de dados
            conn = pymssql.connect(DB_Server, "Leitura", "Leitura", "VGDadosPocas")
            cursor = conn.cursor()
            # SQL -> Validar o código do artigo correspondente à ordem de produção configurada
            SQL = """
                SELECT
                    ArtigoGCP, DescricaoGCP, isnull(CDU_Cadencia, 6000) AS CDU_Cadencia
                FROM
                    VGDadosPocas.dbo.prd_ORDEM_PRODUCAO
                INNER JOIN
                    PRIPOCAS.dbo.Artigo
                ON
                    prd_ORDEM_PRODUCAO.ArtigoGCP = Artigo.Artigo
                WHERE 
                    nEMPRESA = 1 AND
                    NORDEM = replace('{}', '-', '/')
            """.format(ordem)

            # Executar
            cursor.execute(SQL)
            # Retornar dados
            row = cursor.fetchone()

            # Artigo em contagem
            if row is None:
                pass
            else:
                ArtigoEmContagem = row[0]
                DescricaoArtigoEmContagem = str(row[1])
                CadenciaArtigoEmContagem = row[2]

            # Fechar conexão
            conn.close()

            # Ligar à BD
            conn = pymssql.connect(DB_Server, DB_User, DB_Password, DB_DB)
            cursor = conn.cursor()
            # SQL -> Registar a ordem de produção no contador
            SQL = """
                INSERT INTO krones_contadoreslinha
                    (Data, Ativo, Ordem, QuantidadeInicial, Artigo)
                VALUES
                    ('{}', {}, '{}', {}, '{}')
            """.format(
                str(datetime.now().replace(microsecond=0)),
                1,
                ordem,
                cnt,
                ArtigoEmContagem,
            )
            # Executar
            cursor.execute(SQL)
            conn.commit()

            # Buscar ID da Ordem de produção da última ordem
            # SQL -> Buscar o ID da ordem de produção introduzia anteriormente
            SQL = """
                SELECT
                    Id
                FROM
                    krones_contadoreslinha
                WHERE
                    Ativo = 1 AND
                    Ordem = '{}'
            """.format(ordem)
            cursor.execute(SQL)
            row = cursor.fetchone()

            # Registar ID da ordem produção na BD das contagens
            IdBDOrdemProducao = row[0]

            # Fechar conexão
            conn.close()
            # return jsonify(
            #     {"mean": f"Ordem {ordem} configurada com {cnt} garrafas totais"}
            # ), 200
        except Exception as e:
            print(e)


@app.route("/reset-contador", methods=["GET"])
def reset_contador():
    global EstadoContador

    if EstadoContador == 0:
        conn = pymssql.connect(DB_Server, DB_User, DB_Password, DB_DB)
        cursor = conn.cursor()
        # SQL Marcar todas as ordem de produção como inativa
        SQL = """
            UPDATE krones_contadoreslinha
            SET Ativo = 0
            WHERE Ativo = 1
        """

        # Executar
        cursor.execute(SQL)
        conn.commit()

        # Fechar base de dados
        conn.close()

        reset_counter()
        # return jsonify({"mean": "OK"}), 200


@app.route("/status", methods=["GET"])
def status():
    global Ordem
    global ArtigoEmContagem
    global DescricaoArtigoEmContagem
    global CadenciaArtigoEmContagem
    global TempoInicio
    global TempoFim
    global ContagemAtual
    global ContagemTotal
    # global
    global EstatisticaGFANominal
    global Quebras
    global EstadoPorta
    global EstadoContador
    global ContadorConfigurado
    global IdBDOrdemProducao

    data = {
        "Ordem": Ordem,
        "Artigo": ArtigoEmContagem,
        "DescricaoArtigo": DescricaoArtigoEmContagem,
        "CadenciaArtigo": CadenciaArtigoEmContagem,
        "Inicio": TempoInicio,
        "Fim": TempoFim,
        "ContagemAtual": ContagemAtual,
        "ContagemTotal": ContagemTotal,
        "MediaProducao": media_producao(),
        "Nominal": EstatisticaGFANominal,
        "Quebras": Quebras,
        "EstadoPorta": EstadoPorta,
        "EstadoContador": EstadoContador,
        "EstadoConfiguracao": ContadorConfigurado,
        "IdBDOrdemProducao": IdBDOrdemProducao,
    }
    return jsonify({"data": data}), 200


# Função para conectar à base de dados e obter os dados da tabela historico_contagens
def obter_dados_historico(NumPontos, Ordem):
    conn = pymssql.connect(DB_Server, DB_User, DB_Password, DB_DB)
    cursor = conn.cursor(as_dict=True)

    SQL = f"""
        SELECT TOP ({NumPontos})
            DataDados, Ordem, Artigo, DescricaoArtigo, CadenciaArtigo, Inicio, Fim, ContagemAtual, ContagemTotal, MediaProducao, EstimativaFecho, Paragens, Quebras, EstadoPorta, EstadoContador, EstadoConfiguracao, Nominal, Media, Cadencia, Tempo
        FROM krones_historico_contagens
        WHERE Ordem='{Ordem}'
        ORDER BY DataDados ASC
    """

    cursor.execute(SQL)
    result = cursor.fetchall()
    conn.close()

    return result


@app.route("/api/info", defaults={"NumPontos": 180})
@app.route("/api/info/<int:NumPontos>/<string:Ordem>")
def ApiInfo(NumPontos, Ordem=None):
    global EstadoPorta
    global EstadoContador
    global ContadorConfigurado
    global Quebras

    try:
        # Obtém os dados da tabela historico_contagens
        result = obter_dados_historico(NumPontos, Ordem)

        # Dicionário para armazenar os dados consolidados
        consolidated_data = {
            "DataDados": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Ordem": None,
            "Artigo": None,
            "DescricaoArtigo": None,
            "CadenciaArtigo": None,
            "Inicio": None,
            "Fim": None,
            "ContagemAtual": 0,
            "ContagemTotal": 0,
            "MediaProducao": 0.0,
            "EstimativaFecho": "",
            "Nominal": [],
            "Paragens": [],
            "Quebras": 0,
            "EstadoPorta": 0,
            "EstadoContador": 0,
            "EstadoConfiguracao": 0,
            "Media": [],
            "Cadencia": [],
            "Tempo": [],
        }

        # Consolidar os dados das linhas retornadas
        for row in result:
            consolidated_data["Ordem"] = row["Ordem"]
            consolidated_data["Artigo"] = row["Artigo"]
            consolidated_data["DescricaoArtigo"] = row["DescricaoArtigo"]
            consolidated_data["CadenciaArtigo"] = int(row["CadenciaArtigo"])
            consolidated_data["Inicio"] = row["Inicio"] if row["Inicio"] else ""
            consolidated_data["Fim"] = row["Fim"] if row["Fim"] else ""
            consolidated_data["ContagemAtual"] = row["ContagemAtual"]
            consolidated_data["ContagemTotal"] = row["ContagemTotal"]
            consolidated_data["MediaProducao"] += row["MediaProducao"]
            consolidated_data["EstimativaFecho"] = (
                row["EstimativaFecho"] if row["EstimativaFecho"] else ""
            )
            consolidated_data["Quebras"] = Quebras
            consolidated_data["EstadoPorta"] = EstadoPorta
            consolidated_data["EstadoContador"] = EstadoContador
            consolidated_data["EstadoConfiguracao"] = ContadorConfigurado

            consolidated_data["MediaProducao"] = row["MediaProducao"]

            consolidated_data["Paragens"].append(row["Paragens"])
            consolidated_data["Nominal"].append(row["Nominal"])
            consolidated_data["Media"].append(row["Media"])
            consolidated_data["Cadencia"].append(row["Cadencia"])
            consolidated_data["Tempo"].append(row["Tempo"])

        # Retorna os dados consolidados em JSON
        # return jsonify(consolidated_data), 200

    except Exception as e:
        logging.info("Erro na API info: ", e)
        # return jsonify({"error": str(e)}), 500


def contagem():
    global EstadoContador
    global input_state
    global COUNTER_PIN
    global ContagemAtual
    global ContagemTotal
    global Quebras
    global Flop

    logging.info("Thread de contagem iniciado")

    # Loop principal de contagem
    while True:
        # Apenas contar se estado contador = 1
        if EstadoContador == 1:
            # Ler estado do input
            input_state = GPIO.input(COUNTER_PIN)
            # Input ativado quando laser ativado. Primeira parte do Flop/lase
            if input_state == 1 and Flop is False:
                # Levantar FLOP
                Flop = True
            # Input ativado quando laser ativado. Segunda parte do Flop/lase
            if input_state == 0 and Flop is True:
                # Aumentar contagem
                ContagemAtual += 1
                # Verificar se contagem atual atinge contagem total
                if ContagemAtual >= (ContagemTotal + Quebras):
                    parar_contagem()
                # Reset no FLOP
                Flop = False
            # Aguardar tempo para leitura
            time.sleep(0.01)
        else:
            # Aguardar tempo quando nao le
            time.sleep(0.01)


# def gravar_detalhe(Id, Artigo, Tempo, Nominal, Media, Objetivo):
#     # Ligar à BD
#     conn = pymssql.connect(DB_Server, DB_User, DB_Password, DB_DB)
#     cursor = conn.cursor()

#     for a in range(len(Nominal)):
#         # SQL Gravar na BD o detalhe da ordem de produção
#         SQL = """
#             INSERT INTO krones_contadoreslinhadetalhe
#                 (IdContagem, Artigo, DataLeitura, NominalProducao, MediaProducao, Objetivo)
#             VALUES
#                 ({IdOrdem}, '{Artigo}', '{RegistoTempo}', {RegistoNominal}, {RegistoMedia}, {Objetivo})
#         """.format(
#             IdOrdem=int(Id),
#             Artigo=Artigo,
#             RegistoTempo=str(Tempo[a]),
#             RegistoNominal=int(Nominal[a]),
#             RegistoMedia=int(Media[a]),
#             Objetivo=Objetivo,
#         )
#         # Executar
#         cursor.execute(SQL)
#         conn.commit()

#     # Fechar base de dados
#     conn.close()


def gravar_contagem(Id, ContagemAtual):
    try:
        # Conectar ao banco de dados
        conn = pymssql.connect(DB_Server, DB_User, DB_Password, DB_DB)
        cursor = conn.cursor()

        media = media_producao()

        EstimativaTempo = None
        if media != 0 and EstadoContador == 1:
            Minutos = math.ceil((ContagemTotal - ContagemAtual) * 60 / media)
            EstimativaTempo = (datetime.now() + timedelta(minutes=Minutos)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        # Formatar as datas para inserção no SQL
        DataDados = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        Inicio = TempoInicio if TempoInicio else None
        Fim = TempoFim if TempoFim else None
        EstimativaFecho = EstimativaTempo if EstimativaTempo else None

        # Converter datas para string no formato SQL
        Inicio_str = Inicio if Inicio else None
        EstimativaFecho_str = EstimativaFecho if EstimativaFecho else None

        # SQL Gravar na BD o número de garrafas contadas até o momento
        SQL = """
            INSERT INTO krones_contadoreslinhacontagem
                (IdContagem, ContagemAtual, Objetivo, DataLeitura)
            VALUES
                ({IdOrdem}, {ContagemAtual}, {Objetivo}, '{DataLeitura}')
        """.format(
            IdOrdem=int(Id),
            ContagemAtual=int(ContagemAtual),
            Objetivo=int(ContagemTotal),
            DataLeitura=DataDados,
        )

        # Executar
        cursor.execute(SQL)
        conn.commit()

        # Gravar os dados na tabela historico_contagens
        SQL = """
            INSERT INTO krones_historico_contagens
                (DataDados, Ordem, Artigo, DescricaoArtigo, CadenciaArtigo, Inicio, Fim, ContagemAtual, ContagemTotal, MediaProducao, EstimativaFecho, Paragens, Quebras, EstadoPorta, EstadoContador, EstadoConfiguracao, Nominal, Media, Cadencia, Tempo)
            VALUES
                ('{DataDados}', '{Ordem}', '{Artigo}', '{DescricaoArtigo}', {CadenciaArtigo}, '{Inicio}', '{Fim}', {ContagemAtual}, {ContagemTotal}, {MediaProducao}, '{EstimativaFecho}', '{Paragens}', {Quebras}, {EstadoPorta}, {EstadoContador}, {EstadoConfiguracao}, '{Nominal}', '{Media}', '{Cadencia}', '{Tempo}')
        """.format(
            DataDados=DataDados,
            Ordem=Ordem,
            Artigo=ArtigoEmContagem,
            DescricaoArtigo=DescricaoArtigoEmContagem,
            CadenciaArtigo=CadenciaArtigoEmContagem,
            Inicio=Inicio_str,
            Fim=Fim,
            ContagemAtual=ContagemAtual,
            ContagemTotal=ContagemTotal,
            MediaProducao=media_producao(),
            EstimativaFecho=EstimativaFecho_str if EstimativaFecho_str else "",
            Paragens=Paragens[-1],  # array
            Quebras=Quebras,
            EstadoPorta=EstadoPorta,
            EstadoContador=EstadoContador,
            EstadoConfiguracao=ContadorConfigurado,
            Nominal=EstatisticaGFA[-1],  # array
            Media=EstatisticaGFAMedia[-1],  # array
            Cadencia=EstatisticaCadenciaArtigo[-1],
            Tempo=EstatisticaTempo[-1],  # array
        )

        cursor.execute(SQL)
        conn.commit()

        conn.close()

    except Exception as e:
        print(e)


def stats():
    global EstadoContador
    global ContagemAtual
    global EstatisticaGFA
    global EstatisticaGFANominal
    global EstatisticaGFAMedia
    global EstatisticaTempo
    global EstatisticaCadenciaArtigo
    global CadenciaArtigoEmContagem
    global RegistoParagem
    global Paragens
    global Quebras
    global TempoInicio
    global TempoFim
    global Ordem
    global IdBDOrdemProducao
    global ArtigoEmContagem
    global ContadorConfigurado

    # Só grava dados se realmente houver dados para gravar
    GravarDados = 0
    while True:
        # Recolher valores à medida que a produção esta a rolar a cada 10s
        if EstadoContador == 1:
            GravarDados = 1
            contagem = ContagemAtual

            # Aguardar 10 segundos
            time.sleep(10)
            EstatisticaGFA.append((ContagemAtual - contagem) * 360)
            EstatisticaGFANominal = (ContagemAtual - contagem) * 360
            EstatisticaGFAMedia.append(numpy.mean(EstatisticaGFA))

            EstatisticaTempo.append(str(datetime.now().strftime("%H:%M:%S")))
            EstatisticaCadenciaArtigo.append(CadenciaArtigoEmContagem)
            # Registar ocorrencia de paragens
            if RegistoParagem == 1:
                Paragens.append("0")
                RegistoParagem = 0
            else:
                Paragens.append("null")

            # Gravar a contagem atual na nova tabela
            gravar_contagem(IdBDOrdemProducao, ContagemAtual)
        else:
            # Aguardar tempo quando nao le
            time.sleep(0.01)

        # Gravar informação na base de dados quando contagem terminou
        if EstadoContador == 0 and GravarDados == 1:
            GravarDados = 0

            # Ligar à BD
            conn = pymssql.connect(DB_Server, DB_User, DB_Password, DB_DB)
            cursor = conn.cursor()

            # SQL registar informação sobre a contagem na BD
            SQL = """
                UPDATE
                    krones_contadoreslinha
                SET
                    Ativo = 0, QuantidadeFinal = {}, Quebras = {}, MediaProducao = {}, Abertura = '{}', Fecho = '{}'
                WHERE
                    Ativo = 1 AND Ordem = '{}' AND Id = {}
            """.format(
                int(ContagemAtual),
                int(Quebras),
                int(media_producao()),
                str(TempoInicio),
                str(TempoFim),
                str(Ordem),
                str(IdBDOrdemProducao),
            )

            # Executar
            cursor.execute(SQL)
            conn.commit()

            # Fechar ligação à BD
            conn.close()

            # # Gravar o detalhe
            # threading.Thread(
            #     target=gravar_detalhe,
            #     args=(
            #         IdBDOrdemProducao,
            #         ArtigoEmContagem,
            #         EstatisticaTempo,
            #         EstatisticaGFA,
            #         EstatisticaGFAMedia,
            #         CadenciaArtigoEmContagem,
            #     ),
            # ).start()

            # Retirar configuração do contador
            ContadorConfigurado = 0


def auto_pause():
    while True:
        agora = datetime.now()

        # Pausa ao meio-dia
        if agora.hour == 12 and agora.minute == 0:
            pausa()
            time.sleep(60 * 60)  # Pausa de uma hora

        # Pausa ao fim do dia
        elif agora.hour == 17 and agora.minute == 0:
            pausa()
            time.sleep(60 * 60)  # Pausa de uma hora

        else:
            time.sleep(30)


def main():
    try:
        # Iniciar thread do contador
        threading.Thread(target=contagem).start()
        # Iniciar estatistica
        threading.Thread(target=stats).start()
        # Iniciar paragem automática
        threading.Thread(target=auto_pause).start()
    except KeyboardInterrupt:
        GPIO.cleanup()


if __name__ == "__main__":
    try:
        main()
        app.run(host="0.0.0.0", port=3001)
    except KeyboardInterrupt:
        GPIO.cleanup()

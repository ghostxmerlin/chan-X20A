#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <set>
#include <cstdio>
#define SPI_MASTER_ISR_ATTR
constexpr int ESP_OK=0, ESP_ERR_NO_MEM=1, ESP_ERR_INVALID_ARG=2;
constexpr int SPI_TRANS_USE_TXDATA=1, SPI_TRANS_USE_RXDATA=2;
using esp_err_t=int;
struct spi_transaction_t {uint32_t flags=0,length=32,rxlength=32;uint32_t tx_data[1]{},rx_data[1]{};const void* tx_buffer=nullptr;void* rx_buffer=nullptr;};
struct spi_trans_priv_t {spi_transaction_t* trans;const uint32_t* buffer_to_send=nullptr;uint32_t* buffer_to_rcv=nullptr;};
struct spi_bus_attr_t {bool dma_enabled=true;};
struct spi_host_t {spi_bus_attr_t* bus_attr;};
std::set<void*> allocated;
int tx_mode,rx_mode;
void tracked_free(void* p) {if(p){assert(allocated.erase(p)==1);std::free(p);}}
// 0=original DMA buffer, 1=bounce buffer, 2=allocation failure,
// 3=allocation succeeded then cache-sync failed (output pointer is assigned).
esp_err_t setup_dma_priv_buffer(spi_host_t*,uint32_t* original,uint32_t bytes,bool tx,uint32_t,uint32_t** result) {
    const int mode=tx?tx_mode:rx_mode;
    if(mode==2)return ESP_ERR_NO_MEM;
    if(mode==0){*result=original;return ESP_OK;}
    auto* p=static_cast<uint32_t*>(std::malloc(bytes));assert(p);allocated.insert(p);
    std::memset(p,tx?0x11:0x22,bytes);*result=p;
    return mode==3?ESP_ERR_INVALID_ARG:ESP_OK;
}
#define free tracked_free
// DRIVER_FUNCTIONS
#undef free
int main() {
    spi_bus_attr_t bus;spi_host_t host{&bus};int cases=0;
    // Exact device failure: TX bounce allocated, then RX allocation fails.
    uint32_t initial_tx=0xaaaaaaaa,initial_rx=0xbbbbbbbb;
    spi_transaction_t initial;initial.tx_buffer=&initial_tx;initial.rx_buffer=&initial_rx;
    spi_trans_priv_t initial_priv{&initial};tx_mode=1;rx_mode=2;
    assert(setup_priv_desc(&host,&initial_priv)==ESP_ERR_NO_MEM);
    assert(initial_rx==0xbbbbbbbb&&allocated.empty());
    for(int flags=0;flags<4;++flags)for(int present=0;present<4;++present)
    for(tx_mode=0;tx_mode<4;++tx_mode)for(rx_mode=0;rx_mode<4;++rx_mode) {
        uint32_t tx=0xaaaaaaaa,rx=0xbbbbbbbb;
        spi_transaction_t trans;trans.flags=flags;trans.tx_data[0]=tx;trans.rx_data[0]=rx;
        trans.tx_buffer=(present&1)?&tx:nullptr;trans.rx_buffer=(present&2)?&rx:nullptr;
        spi_trans_priv_t priv{&trans};
        const bool has_tx=(flags&SPI_TRANS_USE_TXDATA)||trans.tx_buffer;
        const bool has_rx=(flags&SPI_TRANS_USE_RXDATA)||trans.rx_buffer;
        const bool failed=(has_tx&&tx_mode>=2)||(has_rx&&rx_mode>=2);
        const int result=setup_priv_desc(&host,&priv);
        assert((result!=ESP_OK)==failed);
        auto* original_rx=(flags&SPI_TRANS_USE_RXDATA)?trans.rx_data:static_cast<uint32_t*>(trans.rx_buffer);
        if(!failed)uninstall_priv_desc(&priv);
        if(original_rx)assert(*original_rx==((!failed&&rx_mode==1)?0x22222222:0xbbbbbbbb));
        assert(tx==0xaaaaaaaa&&trans.tx_data[0]==0xaaaaaaaa);
        assert(allocated.empty());++cases;
    }
    std::printf("%d SPI DMA failure/success/inline-buffer cases passed\n",cases);
}
